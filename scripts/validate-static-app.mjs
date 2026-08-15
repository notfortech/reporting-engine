import { readFileSync, statSync } from 'node:fs';
for (const file of ['index.html', 'src/App.jsx', 'src/style.css']) {
  const text = readFileSync(file, 'utf8');
  if (!text.trim()) throw new Error(`${file} is empty`);
  statSync(file);
}
if (!readFileSync('src/App.jsx','utf8').includes('function profileRows')) throw new Error('profiling engine missing');
console.log('Static dashboard application validated.');
