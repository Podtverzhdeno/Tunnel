/** Hermes AutoPilot source-package gate. Run: node --test test_module_integrity.mjs
 * Fail before packaging if MV3 background has a missing static import.
 * No browser, Telegram, credentials or network access required.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {dirname, isAbsolute, relative, resolve, sep} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT=dirname(fileURLToPath(import.meta.url));
const REFERENCE=/\b(?:from\s*|import\s*)['"]((?:\.\/|\.\.\/)[^'"]+)['"]/g;

export function missingStaticImports(root, entry){
  const base=resolve(root),seen=new Set(),missing=[];
  const visit=(filename)=>{
    const full=resolve(base,filename),within=relative(base,full);
    if(isAbsolute(within)||within==='..'||within.startsWith('..'+sep)){
      missing.push({source:filename,dependency:'ESCAPES_PACKAGE'});
      return;
    }
    if(seen.has(full))return;
    seen.add(full);
    if(!existsSync(full)){
      missing.push({source:filename,dependency:'FILE_MISSING'});
      return;
    }
    const source=readFileSync(full,'utf8');
    REFERENCE.lastIndex=0;
    const imports=Array.from(source.matchAll(REFERENCE),match=>match[1]);
    for(const dependency of imports){
      const target=resolve(dirname(full),dependency),rel=relative(base,target);
      if(isAbsolute(rel)||rel==='..'||rel.startsWith('..'+sep)){
        missing.push({source:filename,dependency:'ESCAPES_PACKAGE'});
      }else if(!existsSync(target)){
        missing.push({source:filename,dependency});
      }else{
        visit(rel);
      }
    }
  };
  visit(entry);
  return missing;
}

test('MV3 background resolves every static local module',()=>{
  const manifest=JSON.parse(readFileSync(resolve(ROOT,'manifest.json'),'utf8'));
  assert.equal(manifest.manifest_version,3);
  assert.equal(manifest.background?.type,'module');
  assert.equal(typeof manifest.background?.service_worker,'string');
  assert.deepEqual(
    missingStaticImports(ROOT,manifest.background.service_worker),[]);
});

test('popup entry point and local scripts exist',()=>{
  const manifest=JSON.parse(readFileSync(resolve(ROOT,'manifest.json'),'utf8'));
  const popup=manifest.action?.default_popup;
  assert.equal(typeof popup,'string');
  assert.ok(existsSync(resolve(ROOT,popup)));
  const html=readFileSync(resolve(ROOT,popup),'utf8');
  for(const m of html.matchAll(/<script\b[^>]*\bsrc=['"]([^'"]+)['"]/gi)){
    assert.ok(!m[1].startsWith('/')&&!m[1].includes('..'),'popup script must remain local');
    assert.ok(existsSync(resolve(ROOT,m[1])),`missing popup script: ${m[1]}`);
    assert.deepEqual(missingStaticImports(ROOT,m[1]),[]);
  }
});

test('missing transitive background module is detected',()=>{
  const folder=mkdtempSync(resolve(tmpdir(),'hermes-autopilot-modules-'));
  try{
    writeFileSync(resolve(folder,'background.js'),"import './existing.mjs';\n");
    writeFileSync(resolve(folder,'existing.mjs'),"import './missing.mjs';\n");
    assert.deepEqual(
      missingStaticImports(folder,'background.js'),
      [{source:'existing.mjs',dependency:'./missing.mjs'}]);
    writeFileSync(resolve(folder,'missing.mjs'),'export const ok=true;\n');
    assert.deepEqual(missingStaticImports(folder,'background.js'),[]);
  }finally{rmSync(folder,{recursive:true,force:true});}
});

test('path traversal in module imports fails closed',()=>{
  const folder=mkdtempSync(resolve(tmpdir(),'hermes-autopilot-traversal-'));
  try{
    writeFileSync(resolve(folder,'background.js'),"import '../other/module.mjs';\n");
    assert.deepEqual(missingStaticImports(folder,'background.js'),
      [{source:'background.js',dependency:'ESCAPES_PACKAGE'}]);
  }finally{rmSync(folder,{recursive:true,force:true});}
});
