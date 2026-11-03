const fs=require('fs'),z=require('zlib'),path=require('path');
const root=process.argv[2], out=process.argv[3];
const M=Buffer.from([0x28,0xb5,0x2f,0xfd]);
for(const d of fs.readdirSync(root)){
  const f=path.join(root,d,'session.v4.jsonl.zstd'); if(!fs.existsSync(f)) continue;
  const b=fs.readFileSync(f); const idx=[]; let i=b.indexOf(M);
  while(i>=0){idx.push(i); i=b.indexOf(M,i+1);} idx.push(b.length);
  const parts=[]; let s=0, bad=0;
  for(let k=1;k<idx.length;k++){
    try{ parts.push(z.zstdDecompressSync(b.subarray(idx[s],idx[k]))); s=k; }catch(e){ if(k==idx.length-1) bad++; }
  }
  const t=Buffer.concat(parts); fs.writeFileSync(path.join(out,d+'.jsonl'),t); console.log(d,parts.length,'frames',t.length,'bytes',bad?'TAIL-FAIL':'');
}
