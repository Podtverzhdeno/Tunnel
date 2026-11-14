/** Read-only capacity observation, not a request lease or permission to send. */
export const RELAY_CAPACITY_URL='http://127.0.0.1:18766/v1/readiness';
export function validateCapacity(data){
 const int=(x,min,max)=>Number.isSafeInteger(x)&&x>=min&&x<=max;
 if(!data||data.schema!==1||data.ok!==true||data.readOnly!==true||
    data.sendEnabled!==false||data.integrationConfirmed!==false||
    data.source!=='authenticated_relay_aggregate'||
    !int(data.maxActiveRequests,1,5)||
    !int(data.activeRequests,0,data.maxActiveRequests)||
    !int(data.availableNow,0,data.maxActiveRequests)||
    data.availableNow!==data.maxActiveRequests-data.activeRequests||
    data.advisoryMaxParallelism!==data.maxActiveRequests||
    !int(data.uncertainTotal,0,100000000)||!int(data.blockedTotal,0,100000000)||
    !int(data.queued,0,100000000)){
   throw Error('UNTRUSTED_RELAY_CAPACITY_RESPONSE');
 }
 return Object.freeze({
  schema:1,mode:'READ_ONLY_CAPACITY',
  maxActiveRequests:data.maxActiveRequests,
  activeRequests:data.activeRequests,
  availableNow:data.availableNow,
  advisoryMaxParallelism:data.advisoryMaxParallelism,
  uncertainTotal:data.uncertainTotal,
  blockedTotal:data.blockedTotal,
  queued:data.queued,
  sendEnabled:false,integrationConfirmed:false
 });
}
export async function readRelayCapacity({fetchImpl=fetch,timeoutMs=2500}={}){
 if(!Number.isInteger(timeoutMs)||timeoutMs<200||timeoutMs>5000)throw Error('INVALID_CAPACITY_TIMEOUT');
 let response;
 try{
  response=await fetchImpl(RELAY_CAPACITY_URL,{
    method:'GET',credentials:'omit',cache:'no-store',redirect:'error',
    signal:AbortSignal.timeout(timeoutMs)
  });
 }catch{return {state:'SIDECAR_UNAVAILABLE',readOnly:true,sendEnabled:false};}
 if(!response||response.status!==200)return {
   state:response?.status===503?'RELAY_UNAVAILABLE':'UNEXPECTED_HTTP_STATUS',
   readOnly:true,sendEnabled:false
 };
 try{
  const value=await response.json();
  return {state:'OBSERVED',...validateCapacity(value)};
 }catch{return {state:'INVALID_RESPONSE',readOnly:true,sendEnabled:false};}
}
