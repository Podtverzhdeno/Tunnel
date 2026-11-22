/* Hermes Temporary transport v2: optional DOM reconcile scheduler.
 * Detached experimental helper, NOT imported by the live Web Companion.
 *
 * A periodic 350ms whole-document DOM walk burns CPU on idle background tabs.
 * MutationObserver alone can burn CPU on every streamed token and may miss
 * non-DOM state changes. This scheduler COALESCES mutation bursts and runs a
 * bounded periodic fallback. A real UI verifier must still enforce account,
 * tab, document and temporary-mode ownership on EVERY Send.
 *
 * No browser/ChatGPT side effects; never authorizes Send or reads responses.
 */
"use strict";
function makeReconcileBudget({scan,Observer,target,now=Date.now,
                              schedule=setTimeout,cancel=clearTimeout,
                              minGapMs=1200,heartbeatMs=5000,onError=()=>{}}){
  if(typeof scan!=="function"||typeof Observer!=="function"||!target||
     typeof now!=="function"||typeof schedule!=="function"||
     typeof cancel!=="function"||!Number.isFinite(minGapMs)||
     !Number.isFinite(heartbeatMs)||minGapMs<300||
     heartbeatMs<minGapMs||heartbeatMs>60000)throw Error("invalid_scheduler_config");
  let running=false,observer=null,timer=null,due=Infinity;
  let scanning=false,dirty=false,last=Number.NEGATIVE_INFINITY;
  let completed=0,errors=0;
  function setDue(at){
    if(!running || (timer!==null && at>=due))return;
    if(timer!==null)cancel(timer);
    due=at;
    timer=schedule(()=>{timer=null;due=Infinity;void tick();},
                   Math.max(0,at-now()));
  }
  function request(){
    if(!running)return;
    if(scanning){dirty=true;return;}
    setDue(Math.max(now(),last+minGapMs));
  }
  async function tick(){
    if(!running||scanning)return;
    scanning=true;dirty=false;
    try{await scan();completed++;}
    catch(e){errors++;try{onError(e);}catch(_){}}
    finally{
      last=now();scanning=false;
      if(running){
        // Coalesced changes arriving DURING the async scan are important.
        // No changes -> low-cost safety/account/heartbeat reconciliation.
        setDue(last+(dirty?minGapMs:heartbeatMs));
      }
    }
  }
  return {
    start(){
      if(running)return false;
      running=true;
      observer=new Observer(request);
      try{
        observer.observe(target,{subtree:true,childList:true,
                                 characterData:true,attributes:true});
      }catch(e){
        running=false;observer.disconnect();observer=null;throw e;
      }
      request();return true;
    },
    stop(){
      if(!running)return false;
      running=false;dirty=false;
      if(observer){observer.disconnect();observer=null;}
      if(timer!==null){cancel(timer);timer=null;due=Infinity;}
      return true;
    },
    signal:request,
    stats:()=>({running,completed,errors,scanning,
                pending:timer!==null,dirty})
  };
}
module.exports={makeReconcileBudget};
