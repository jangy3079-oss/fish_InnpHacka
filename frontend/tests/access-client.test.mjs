import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from '../node_modules/esbuild/lib/main.js';

const root=fileURLToPath(new URL('..',import.meta.url));
const bundle=await build({ stdin:{contents:"export {api} from './src/api/client.js'; export * from './src/auth/session.js';",resolveDir:root},
  bundle:true,write:false,format:'esm',platform:'node',loader:{'.svg':'text'},
  define:{'import.meta.env':JSON.stringify({VITE_USE_MOCK:'false',VITE_API_BASE:'http://localhost',VITE_SUPABASE_URL:'https://auth.test',VITE_SUPABASE_PUBLISHABLE_KEY:'public-key'})},
  plugins:[{name:'raw',setup(b){
    b.onResolve({filter:/\?raw$/},(a)=>({path:resolve(a.resolveDir,a.path.slice(0,-4)),namespace:'raw'}));
    b.onLoad({filter:/.*/,namespace:'raw'},async(a)=>({contents:await readFile(a.path,'utf8'),loader:'text'}));
  }}]});
const {api,signIn,clearSession}=await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('real session: refresh once, stable UUID, forbidden event, authenticated blob and raw source',async()=>{
  const original={fetch:globalThis.fetch,storage:globalThis.sessionStorage,window:globalThis.window,
    create:URL.createObjectURL,revoke:URL.revokeObjectURL};
  const values=new Map(),bodies=[]; let refreshes=0,denials=0,revoked=0;
  globalThis.sessionStorage={getItem:(k)=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:(k)=>values.delete(k)};
  globalThis.window=new EventTarget();
  window.addEventListener('protected-data-forbidden',()=>denials++);
  const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'content-type':'application/json'}});
  globalThis.fetch=async(url,options)=>{
    if(url.includes('grant_type=password')) return json({access_token:'old',refresh_token:'refresh'});
    if(url.includes('grant_type=refresh_token')) {refreshes++; return json({access_token:'new',refresh_token:'rotated'});}
    if(url.endsWith('/diagnosis-request')) bodies.push(options.body);
    const token=options.headers.Authorization;
    if(token==='Bearer old') return json({detail:{code:'invalid_token'}},401);
    assert.equal(token,'Bearer new');
    if(url.endsWith('/stock')) return json({detail:{code:'stock_forbidden'}},403);
    if(url.endsWith('/echogram')) return new Response(new Uint8Array([1,2,3]),{headers:{'content-type':'image/png'}});
    if(url.includes('/timeseries')) {
      const parsed=new URL(url);
      assert.equal(parsed.searchParams.get('source'),'sonar'); assert.equal(parsed.searchParams.get('limit'),'1000');
      return json({points:[{ts:'2026-08-12T00:00:00Z',value:0.4,source:'sonar',confidence:0.8}],has_more:true});
    }
    return json({persisted:true});
  };
  URL.createObjectURL=(blob)=>{assert.equal(blob.type,'image/png');return 'blob:protected';};
  URL.revokeObjectURL=(url)=>{assert.equal(url,'blob:protected');revoked++;};
  try{
    await signIn('owner@example.invalid','test-password');
    await Promise.all([api.getMe(),api.requestDiagnosis(3)]);
    assert.equal(refreshes,1); assert.equal(bodies.length,2); assert.equal(bodies[0],bodies[1]);
    await assert.rejects(api.getStock(3),(e)=>e.status===403);
    assert.equal(denials,1); assert.equal(refreshes,1);
    const image=await api.getEchogram(3); image.revoke(); assert.equal(revoked,1);
    const series=await api.getTimeseries(3,'activity');
    assert.equal(series.points[0].confidence,0.8); assert.equal(series.raw,true); assert.equal(series.has_more,true);
  }finally{
    clearSession(); globalThis.fetch=original.fetch; globalThis.sessionStorage=original.storage; globalThis.window=original.window;
    URL.createObjectURL=original.create; URL.revokeObjectURL=original.revoke;
  }
});
