'use strict';

const fs = require('node:fs');
const path = require('node:path');

const directoryNames = Object.freeze({
  src: 'src',
  dist: 'dist',
  examples: 'examples',
  tests: 'tests'
});

function walkFiles(root, current = root) {
  return fs.readdirSync(current, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const absolutePath = path.join(current, entry.name);
      if (entry.name === '__pycache__' || entry.name.endsWith('.pyc')) {
        return [];
      }
      return entry.isDirectory() ? walkFiles(root, absolutePath) : [absolutePath];
    });
}

function indexDirectory(relativePath) {
  const root = path.join(__dirname, relativePath);
  const files = walkFiles(root);
  return Object.freeze({
    root,
    files: Object.freeze(files),
    relativeFiles: Object.freeze(
      files.map((file) => path.relative(root, file).split(path.sep).join('/'))
    )
  });
}

const directories = Object.freeze(
  Object.fromEntries(
    Object.entries(directoryNames).map(([name, relativePath]) => [
      name,
      indexDirectory(relativePath)
    ])
  )
);

function resolve(directoryName, ...segments) {
  const directory = directories[directoryName];
  if (!directory) {
    throw new RangeError(`unknown pypolycall directory: ${directoryName}`);
  }

  const resolved = path.resolve(directory.root, ...segments);
  const prefix = `${directory.root}${path.sep}`;
  if (resolved !== directory.root && !resolved.startsWith(prefix)) {
    throw new RangeError(`path escapes pypolycall ${directoryName} directory`);
  }
  return resolved;
}

module.exports = Object.freeze({
  packageName: 'pypolycall',
  directories,
  resolve,
  pyproject: path.join(__dirname, 'pyproject.toml'),
  config: path.join(__dirname, 'pypolycallrc'),
  manifest: path.join(__dirname, 'polycall-binding.json')
});
