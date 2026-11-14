/** Read-only Hermes service diagnostics. No credentials, no sends, no auto-retry. */
export const SERVICE_PROBES=Object.freeze([
  Object.freeze({id:'relay',label:'Hermes Relay',url:'http://127.0.0.1:18765/health'}),
  Object.freeze({id:'companion_bridge',label:'Web Companion Bridge',url:'http://127.0.0.1:18763/v1/health'})
]);

const cleanError=error=>{
  const name=String(error?.name||'');
  if(name==='AbortError'||name==='TimeoutError')return 'TIMEOUT';
  return 'UNREACHABLE_OR_BLOCKED';
};
const classify=(id,status,body)=>{
  if(status===401||status===403)return 'AUTH_REQUIRED';
  if(status!==200)return 'UNEXPECTED_HTTP_STATUS';
  if(id==='relay'&&body?.ok!==true)return 'UNEXPECTED_HEALTH_RESPONSE';
  if(id==='companion_bridge'&&body?.ok!==true)return 'UNEXPECTED_HEALTH_RESPONSE';
  return 'REACHABLE';
};
export async function probeCompanion({fetchImpl=fetch,timeoutMs=1800}={}){
  if(!Number.isInteger(timeoutMs)||timeoutMs<250||timeoutMs>5000)throw new Error('invalid_probe_timeout');
  const results=[];
  for(const target of SERVICE_PROBES){
    let result={id:target.id,label:target.label,state:'UNREACHABLE_OR_BLOCKED',httpStatus:null};
    try{
      const response=await fetchImpl(target.url,{
        method:'GET',cache:'no-store',credentials:'omit',redirect:'error',
        signal:AbortSignal.timeout(timeoutMs)
      });
      result.httpStatus=Number.isInteger(response.status)?response.status:null;
      let body=null;
      if(response.status===200){
        try{body=await response.json();}catch{body=null;}
      }
      result.state=classify(target.id,response.status,body);
    }catch(error){result.state=cleanError(error);}
    results.push(result);
  }
  return {schema:1,mode:'READ_ONLY_DIAGNOSTIC',sendEnabled:false,
    integrationConfirmed:false,services:results};
}
