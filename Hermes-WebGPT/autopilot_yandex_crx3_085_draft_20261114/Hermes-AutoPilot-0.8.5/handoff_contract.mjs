/** Stable, side-effect-free preview of a logical Hermes Relay turn.
 * This is NOT a claim, lease, message send, completion ACK, or proof of ownership
 * in the independent Web Companion extension.
 */
import {validateRunSnapshot} from './supervisor.mjs';
const SAFE_ID=/^[A-Za-z0-9_-]{1,80}$/;
const HEX=/^[0-9a-f]{64}$/;
const enc=new TextEncoder();
const hex=bytes=>Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
const valid=(s)=>typeof s==='string'&&SAFE_ID.test(s);
export async function deriveTurnId({runId,workerId,taskId,epoch},digest=crypto.subtle.digest.bind(crypto.subtle)){
 if(!valid(runId)||!valid(workerId)||!valid(taskId)||
    !Number.isSafeInteger(epoch)||epoch<1||epoch>Number.MAX_SAFE_INTEGER){
  throw Error('INVALID_TURN_IDENTITY');
 }
 // Canonical unambiguous tuple avoids truncation collisions. The tab ID is
 // deliberately excluded: a navigation or browser restart cannot make a new turn.
 const canonical=JSON.stringify(['autopilot-relay-turn-v1',runId,workerId,taskId,epoch]);
 const sha=hex(await digest('SHA-256',enc.encode(canonical)));
 if(!HEX.test(sha))throw Error('INVALID_CRYPTO_DIGEST');
 return 'ap1-'+sha; // 68 chars; Relay turn_id accepts 12..128 safe chars
}
export async function previewTurnBinding(run,{workerId,taskId,epoch,tabId}){
 validateRunSnapshot(run);
 if(run.status!=='ACTIVE')throw Error('RUN_NOT_ACTIVE');
 if(!valid(workerId)||!valid(taskId)||!Number.isSafeInteger(epoch)||epoch<1||
    !Number.isSafeInteger(tabId)||tabId<1)throw Error('INVALID_LEASE');
 const slot=run.slots.find(x=>x.id===workerId);
 const task=run.tasks.find(x=>x.id===taskId);
 if(!slot||!task||slot.taskId!==taskId||slot.epoch!==epoch||
    slot.tabId!==tabId||slot.phase!=='WORKING'||task.status!=='RUNNING'){
  throw Error('STALE_OR_UNOWNED_LEASE');
 }
 if(slot.pendingSend)throw Error('UNKNOWN_SEND_REQUIRES_RECONCILIATION');
 return Object.freeze({
  schema:'autopilot-relay-turn-preview-v1',
  runId:run.id,workerId,taskId,epoch,tabId,
  turnId:await deriveTurnId({runId:run.id,workerId,taskId,epoch}),
  state:'PREVIEW_ONLY',relayBound:false,leaseOwnedByRelay:false,
  sendEnabled:false,independentVerification:false,
 });
}
