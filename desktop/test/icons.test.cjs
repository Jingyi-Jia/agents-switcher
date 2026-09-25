'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const assets = path.resolve(__dirname, '../assets');
const read = (name) => fs.readFileSync(path.join(assets, name));
const cyan = [120, 220, 232, 255];
const violet = [180, 155, 244, 255];
const silhouette = [
  '................',
  '................',
  '................',
  '..........#.....',
  '...#########....',
  '..###########...',
  '..##......##....',
  '..##......#.....',
  '.....#......##..',
  '....##......##..',
  '...###########..',
  '....#########...',
  '.....#..........',
  '................',
  '................',
  '................',
];

function png(data) {
  assert.deepEqual(data.subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const chunks = new Map();
  let offset = 8;
  while (offset < data.length) {
    const length = data.readUInt32BE(offset);
    const type = data.toString('ascii', offset + 4, offset + 8);
    assert(offset + 12 + length <= data.length);
    chunks.set(type, [...(chunks.get(type) || []), data.subarray(offset + 8, offset + 8 + length)]);
    offset += length + 12;
  }
  assert.equal(offset, data.length);
  assert.equal(chunks.get('IEND')[0].length, 0);
  const header = chunks.get('IHDR')[0];
  const width = header.readUInt32BE(0);
  const height = header.readUInt32BE(4);
  assert.deepEqual([...header.subarray(8)], [8, 6, 0, 0, 0]);
  const stride = width * 4;
  const raw = zlib.inflateSync(Buffer.concat(chunks.get('IDAT')));
  assert.equal(raw.length, height * (stride + 1));
  const pixels = Buffer.alloc(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    assert(filter <= 4);
    for (let x = 0; x < stride; x += 1) {
      const index = y * stride + x;
      const left = x >= 4 ? pixels[index - 4] : 0;
      const up = y > 0 ? pixels[index - stride] : 0;
      const corner = x >= 4 && y > 0 ? pixels[index - stride - 4] : 0;
      const p = left + up - corner;
      const distances = [Math.abs(p - left), Math.abs(p - up), Math.abs(p - corner)];
      const paeth = distances[0] <= distances[1] && distances[0] <= distances[2] ? left
        : distances[1] <= distances[2] ? up : corner;
      const predictor = [0, left, up, Math.floor((left + up) / 2), paeth][filter];
      pixels[index] = (raw[y * (stride + 1) + x + 1] + predictor) & 255;
    }
  }
  return { width, height, chunks, pixels };
}

function pixel(image, x, y) {
  const index = (y * image.width + x) * 4;
  return [...image.pixels.subarray(index, index + 4)];
}

function checkSilhouette(image, size) {
  assert.equal(image.width, size);
  assert.equal(image.height, size);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const expected = silhouette[Math.floor(y * 16 / size)][Math.floor(x * 16 / size)] === '#' ? 255 : 0;
      assert.equal(pixel(image, x, y)[3], expected, `alpha at ${x},${y} in ${size}px glyph`);
    }
  }
}

function checkDock(image, size) {
  assert.equal(image.width, size);
  assert.equal(image.height, size);
  for (const [x, y] of [[0, 0], [size - 1, 0], [0, size - 1], [size - 1, size - 1]]) {
    assert.equal(pixel(image, x, y)[3], 0);
  }
  const center = pixel(image, Math.floor(size / 2), Math.floor(size / 2));
  assert.equal(center[3], 255);
  assert(center.slice(0, 3).every((value) => value < 80));
  const glyphSize = size >= 32 ? Math.round(size * 7 / 8) : size;
  const offset = Math.floor((size - glyphSize) / 2);
  for (let y = 0; y < glyphSize; y += 1) {
    for (let x = 0; x < glyphSize; x += 1) {
      const row = Math.floor((y + 0.5) * 16 / glyphSize);
      const column = Math.floor((x + 0.5) * 16 / glyphSize);
      if (silhouette[row][column] === '#') {
        assert.deepEqual(pixel(image, x + offset, y + offset), row < 8 ? cyan : violet,
          `crisp mark at ${x},${y} in ${size}px Dock icon`);
      }
    }
  }
}

test('macOS template PNGs are black alpha masks with no tile, fringe, or filled center', () => {
  for (const [name, size, pixelsPerMeter] of [
    ['trayTemplate.png', 16, 2835],
    ['trayTemplate@2x.png', 32, 5669],
  ]) {
    const image = png(read(name));
    checkSilhouette(image, size);
    for (let index = 0; index < image.pixels.length; index += 4) {
      assert.deepEqual([...image.pixels.subarray(index, index + 3)], [0, 0, 0]);
    }
    const density = image.chunks.get('pHYs')[0];
    assert.equal(density.readUInt32BE(0), pixelsPerMeter);
    assert.equal(density.readUInt32BE(4), pixelsPerMeter);
    assert.equal(density[8], 1);
    assert.deepEqual([...image.chunks.get('sRGB')[0]], [0]);
  }
});

test('non-template tray is the same transparent monochrome glyph, readable on light and dark', () => {
  const image = png(read('tray.png'));
  checkSilhouette(image, 32);
  const colors = new Set();
  for (let index = 0; index < image.pixels.length; index += 4) {
    if (image.pixels[index + 3]) colors.add([...image.pixels.subarray(index, index + 3)].join(','));
  }
  assert.equal(colors.size, 1);
  const foreground = [...colors][0].split(',').map(Number);
  const luminance = (rgb) => rgb.map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  for (const background of [[251, 252, 254], [233, 237, 243], [23, 28, 38], [36, 43, 57]]) {
    const light = Math.max(luminance(foreground), luminance(background));
    const dark = Math.min(luminance(foreground), luminance(background));
    assert((light + 0.05) / (dark + 0.05) >= 3, `contrast against ${background}`);
  }
});

test('Dock PNG is RGBA artwork with transparent corners and a sharp two-color mark', () => {
  checkDock(png(read('icon.png')), 1024);
});

test('Windows ICO contains individually rendered, sharp RGBA icons at every declared size', () => {
  const data = read('icon.ico');
  assert.equal(data.readUInt16LE(0), 0);
  assert.equal(data.readUInt16LE(2), 1);
  const sizes = [16, 24, 32, 48, 64, 128, 256];
  assert.equal(data.readUInt16LE(4), sizes.length);
  let end = 6 + sizes.length * 16;
  for (const [index, size] of sizes.entries()) {
    const entry = 6 + index * 16;
    assert.equal(data[entry] || 256, size);
    assert.equal(data[entry + 1] || 256, size);
    assert.equal(data.readUInt16LE(entry + 6), 32);
    const length = data.readUInt32LE(entry + 8);
    const offset = data.readUInt32LE(entry + 12);
    assert.equal(offset, end);
    checkDock(png(data.subarray(offset, offset + length)), size);
    end = offset + length;
  }
  assert.equal(end, data.length);
});

test('macOS ICNS includes native 16px through 1024px artwork and Retina representations', () => {
  const data = read('icon.icns');
  assert.equal(data.toString('ascii', 0, 4), 'icns');
  assert.equal(data.readUInt32BE(4), data.length);
  const sizes = new Map(Object.entries({
    icp4: 16, icp5: 32, icp6: 64, ic07: 128, ic08: 256, ic09: 512,
    ic10: 1024, ic11: 32, ic12: 64, ic13: 256, ic14: 512,
  }));
  const headers = [];
  let table;
  let offset = 8;
  while (offset < data.length) {
    const kind = data.toString('ascii', offset, offset + 4);
    const length = data.readUInt32BE(offset + 4);
    assert(length >= 8 && offset + length <= data.length);
    if (kind === 'TOC ') {
      assert.equal(table, undefined);
      table = data.subarray(offset + 8, offset + length);
    } else {
      assert(sizes.has(kind), kind);
      checkDock(png(data.subarray(offset + 8, offset + length)), sizes.get(kind));
      sizes.delete(kind);
      headers.push(data.subarray(offset, offset + 8));
    }
    offset += length;
  }
  assert.equal(offset, data.length);
  assert.equal(sizes.size, 0);
  assert.deepEqual(table, Buffer.concat(headers));
});

test('inline SVG uses the same pixel silhouette and palette without a background or external content', () => {
  const svg = read('mark.svg').toString('utf8');
  assert.match(svg, /viewBox="0 0 16 16"/);
  assert.match(svg, /shape-rendering="crispEdges"/);
  assert.deepEqual([...svg.matchAll(/<\/?([a-z][\w:-]*)\b/g)].map((match) => match[1]), ['svg', 'path', 'path', 'svg']);
  const paths = [...svg.matchAll(/<path fill="(#[a-f0-9]{6})" d="([^"]+)"\/>/g)];
  assert.deepEqual(paths.map((match) => match[1]), ['#78dce8', '#b49bf4']);
  const image = { width: 16, height: 16, pixels: Buffer.alloc(16 * 16 * 4) };
  for (const [index, match] of paths.entries()) {
    const rectangles = [...match[2].matchAll(/M(\d+) (\d+)h(\d+)v1H(\d+)z/g)];
    assert.equal(rectangles.map((rectangle) => rectangle[0]).join(''), match[2]);
    for (const rectangle of rectangles) {
      const [, left, y, width, close] = rectangle.map(Number);
      assert.equal(left, close);
      assert(left >= 0 && left + width <= 16 && y >= 0 && y < 16);
      for (let x = left; x < left + width; x += 1) {
        image.pixels.set(index === 0 ? cyan : violet, (y * 16 + x) * 4);
      }
    }
  }
  checkSilhouette(image, 16);
  for (let y = 0; y < 16; y += 1) {
    for (let x = 0; x < 16; x += 1) {
      if (silhouette[y][x] === '#') assert.deepEqual(pixel(image, x, y), y < 8 ? cyan : violet);
    }
  }
});
