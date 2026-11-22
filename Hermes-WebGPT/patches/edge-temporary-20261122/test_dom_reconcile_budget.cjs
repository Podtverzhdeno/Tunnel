"use strict";
const assert=require("node:assert/strict");
const {makeReconcileBudget}=require("./dom_reconcile_budget.cjs");
async function scenario(){
  let current=0,next=0,timers=new Map(),observers=[];
  const clock=()=>current;
  const schedule=(fn,delay)=>{
    const id=++next;timers.set(id,{fn,due:current+delay});return id;
  };
  const cancel=id=>timers.delete(id);
  class Observer{
    constructor(cb){this.cb=cb;this.closed=false;observers.push(this);}
    observe(target,config){
      assert.ok(target);assert.equal(config.subtree,true);
      assert.equal(config.characterData,true);
    }
    disconnect(){this.closed=true;}
    mutation(){if(!this.closed)this.cb();}
  }
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const advance=async until=>{
    if(until<current)throw Error("time_backwards");
    for(let k=0;k<100;k++){
      let nextItem=null;
      for(const [id,item] of timers){
        if(item.due<=until && (!nextItem || item.due<nextItem.item.due))
          nextItem={id,item};
      }
      if(!nextItem)break;
      timers.delete(nextItem.id);
      current=nextItem.item.due;
      nextItem.item.fn();await flush();
      if(k===99)throw Error("unbounded_rescheduling_loop");
    }
    current=until;await flush();
  };
  let scans=0;
  const driver=makeReconcileBudget({
    scan:async()=>{scans++;},
    Observer,target:{},now:clock,schedule,cancel,
    minGapMs:1200,heartbeatMs:5000
  });
  assert.equal(driver.start(),true);assert.equal(driver.start(),false);
  await advance(0);assert.equal(scans,1);
  assert.equal(observers.length,1);
  await advance(100);
  for(let i=0;i<1000;i++)observers[0].mutation();
  assert.equal(timers.size,1,"all 1000 mutations coalesce to one timer");
  await advance(1199);assert.equal(scans,1);
  await advance(1200);assert.equal(scans,2,"mutation run no sooner than 1200ms");
  assert.equal(driver.stats().completed,2);
  await advance(6199);assert.equal(scans,2,"idle does not run at 350ms intervals");
  await advance(6200);assert.equal(scans,3,"bounded 5 second fallback");
  assert.equal(driver.stop(),true);assert.equal(driver.stop(),false);
  assert.equal(observers[0].closed,true);
  await advance(20000);assert.equal(scans,3,"no work after stopped");

  // Slow async scan: a mutation arriving mid-scan MUST be remembered, not
  // dropped or processed concurrently and never becomes a hot retry.
  let release=null,midScans=0;
  const slow=makeReconcileBudget({
    scan:()=>{midScans++;return new Promise(resolve=>{release=resolve;});},
    Observer,target:{},now:clock,schedule,cancel,minGapMs:1200,heartbeatMs:5000
  });
  slow.start();await advance(20000);
  assert.equal(midScans,1);assert.equal(slow.stats().scanning,true);
  const ob=observers.at(-1);
  for(let i=0;i<500;i++)ob.mutation();
  assert.equal(midScans,1);
  release();await flush();
  assert.equal(slow.stats().scanning,false);
  await advance(21199);assert.equal(midScans,1);
  await advance(21200);assert.equal(midScans,2);
  slow.stop();release();await flush();
  assert.equal(timers.size,0);

  let errs=0,errored=0;
  const bad=makeReconcileBudget({
    scan:async()=>{errored++;throw Error("transient DOM scan failure");},
    Observer,target:{},now:clock,schedule,cancel,minGapMs:1200,
    heartbeatMs:5000,onError:()=>{errs++;}
  });
  bad.start();await advance(21200);
  assert.equal(errs,1);assert.equal(errored,1);
  await advance(26200);assert.equal(errs,2,"scan failures cannot hot-loop");
  bad.stop();
  assert.equal(timers.size,0);
  console.log("PASS: 1000 streamed mutations coalesced, 5s fallback, async wakeup, stop, error backoff");
}
scenario().catch(e=>{console.error(e);process.exitCode=1});
