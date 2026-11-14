/** Hermes AutoPilot media handoff contract v1.
 * Offline planner only. Does NOT attach files, send ChatGPT messages, or execute
 * Telegram uploads. Caller must persist UNKNOWN before attempting an external upload.
 */
export const SCHEMA='hermes-media-transfer-v1';
export const DIRECTIONS=Object.freeze(['TELEGRAM_TO_CHATGPT','CHATGPT_TO_TELEGRAM']);
export const MAX_IMAGE_BYTES=20*1024*1024; // internal conservative policy, NOT provider size limit
export const MAX_FILE_BYTES=30*1024*1024;  // internal conservative policy, NOT provider size limit
export const MIME_EXTENSIONS=Object.freeze({
 'image/png':Object.freeze(['.png']),
 'image/jpeg':Object.freeze(['.jpg','.jpeg']),
 'image/webp':Object.freeze(['.webp']),
 'application/pdf':Object.freeze(['.pdf']),
 'text/plain':Object.freeze(['.txt']),
 'text/csv':Object.freeze(['.csv']),
});
const TURN_ID=/^ap1-[0-9a-f]{64}$/;
const SHA=/^[0-9a-f]{64}$/;
const SAFE_ID=/^[A-Za-z0-9_-]{1,80}$/;
const EVENTS=Object.freeze(['UPLOAD_STARTED','ATTACHMENT_OBSERVED','OWNER_RECONCILED','CANCEL_STAGED']);
const PHASES=Object.freeze(['STAGED','UNKNOWN','OBSERVED_UNVERIFIED','MANUAL_RECONCILED','CANCELLED']);
const allowed=(value,keys)=>{
 if(!value||typeof value!=='object'||Array.isArray(value))throw Error('INVALID_OBJECT');
 const proto=Object.getPrototypeOf(value);
 if(proto!==Object.prototype&&proto!==null)throw Error('INVALID_OBJECT_PROTOTYPE');
 if(Object.keys(value).some(x=>!keys.includes(x)))throw Error('UNEXPECTED_FIELD');
};
const bytesOf=input=>{
 if(input instanceof Uint8Array)return input;
 if(input instanceof ArrayBuffer)return new Uint8Array(input);
 throw Error('UNSUPPORTED_BYTES');
};
const toHex=data=>Array.from(new Uint8Array(data),x=>x.toString(16).padStart(2,'0')).join('');
const digestDefault=(alg,value)=>globalThis.crypto.subtle.digest(alg,value);
const hash=async(data,digest=digestDefault)=>{
 const result=toHex(await digest('SHA-256',data));
 if(!SHA.test(result))throw Error('BAD_HASH_IMPLEMENTATION');
 return result;
};
const textEncoder=new TextEncoder();
function validateName(name,mime){
 if(typeof name!=='string'||name!==name.normalize('NFC')||name.length<1||name.length>128||
    !/^[\p{L}\p{N}][\p{L}\p{N}\p{M} ._()\-]*$/u.test(name)||
    name.includes('..')||/[. ]$/.test(name)||/[\/\\\u0000-\u001f\u007f]/.test(name))
   throw Error('UNSAFE_FILENAME');
 const extensions=MIME_EXTENSIONS[mime];
 if(!extensions.some(ext=>name.toLowerCase().endsWith(ext)))throw Error('MIME_EXTENSION_MISMATCH');
 return name;
}
export function validateMediaDescriptor(value){
 const keys=['turnId','attachmentId','direction','fileName','mimeType','sizeBytes','sha256','leaseEpoch'];
 allowed(value,keys);
 if(keys.some(k=>!Object.hasOwn(value,k)))throw Error('MISSING_DESCRIPTOR_FIELD');
 if(typeof value.turnId!=='string'||!TURN_ID.test(value.turnId)||
    typeof value.attachmentId!=='string'||!SAFE_ID.test(value.attachmentId))
   throw Error('INVALID_ATTACHMENT_IDENTITY');
 if(!DIRECTIONS.includes(value.direction))throw Error('INVALID_DIRECTION');
 if(typeof value.mimeType!=='string'||!Object.hasOwn(MIME_EXTENSIONS,value.mimeType))
   throw Error('UNSUPPORTED_MIME');
 validateName(value.fileName,value.mimeType);
 const limit=value.mimeType.startsWith('image/')?MAX_IMAGE_BYTES:MAX_FILE_BYTES;
 if(!Number.isSafeInteger(value.sizeBytes)||value.sizeBytes<1||value.sizeBytes>limit)
   throw Error('MEDIA_SIZE_POLICY');
 if(typeof value.sha256!=='string'||!SHA.test(value.sha256))
   throw Error('INVALID_SHA256');
 if(!Number.isSafeInteger(value.leaseEpoch)||value.leaseEpoch<1)
   throw Error('INVALID_LEASE_EPOCH');
 return Object.freeze(Object.fromEntries(keys.map(k=>[k,value[k]])));
}
function signatureOk(mime,buf){
 const prefix=(...a)=>a.every((x,i)=>buf[i]===x);
 if(mime==='image/png')return buf.length>=8&&prefix(137,80,78,71,13,10,26,10);
 if(mime==='image/jpeg')return buf.length>=4&&prefix(255,216,255);
 if(mime==='image/webp')return buf.length>=12&&
   prefix(82,73,70,70)&&buf[8]===87&&buf[9]===69&&buf[10]===66&&buf[11]===80;
 if(mime==='application/pdf')return buf.length>=5&&prefix(37,80,68,70,45);
 if(mime==='text/plain'||mime==='text/csv'){
  if(buf.includes(0))return false;
  try{new TextDecoder('utf-8',{fatal:true}).decode(buf);return true;}
  catch{return false;}
 }
 return false;
}
export async function verifyMediaBytes(descriptor,rawBytes,{digest=digestDefault}={}){
 const data=validateMediaDescriptor(descriptor);
 const bytes=bytesOf(rawBytes);
 if(bytes.byteLength!==data.sizeBytes)throw Error('MEDIA_SIZE_MISMATCH');
 if(!signatureOk(data.mimeType,bytes))throw Error('MEDIA_SIGNATURE_MISMATCH');
 const actual=await hash(bytes,digest);
 if(actual!==data.sha256)throw Error('MEDIA_DIGEST_MISMATCH');
 return Object.freeze({bytesConfirmed:true,sha256:actual,sizeBytes:bytes.byteLength,
   signatureChecked:true, malwareScanned:false,providerAccepted:false});
}
export async function deriveMediaTransferId(descriptor,{digest=digestDefault}={}){
 const data=validateMediaDescriptor(descriptor);
 const identity=JSON.stringify([SCHEMA,data.turnId,data.attachmentId,data.direction]);
 return 'hm1-'+await hash(textEncoder.encode(identity),digest);
}
export async function stageMediaTransfer(descriptor,rawBytes,{digest=digestDefault}={}){
 const data=validateMediaDescriptor(descriptor);
 await verifyMediaBytes(data,rawBytes,{digest});
 const id=await deriveMediaTransferId(data,{digest});
 return Object.freeze({schema:SCHEMA,transferId:id,descriptor:data,
   state:'STAGED',events:Object.freeze([]),revision:0,
   uploadAllowed:false,uploadPerformed:false,
   externalAttachmentVerified:false,independentChatProof:false});
}
const eventFields=['eventId','transferId','turnId','leaseEpoch','type','actor','evidenceSha256'];
const normalizeEvent=(event)=>{
 allowed(event,eventFields);
 if(eventFields.slice(0,6).some(x=>!Object.hasOwn(event,x)))throw Error('MISSING_EVENT_FIELD');
 if(typeof event.eventId!=='string'||!SAFE_ID.test(event.eventId)||
    typeof event.transferId!=='string'||!/^hm1-[0-9a-f]{64}$/.test(event.transferId)||
    typeof event.turnId!=='string'||!TURN_ID.test(event.turnId)||
    !Number.isSafeInteger(event.leaseEpoch)||event.leaseEpoch<1||
    typeof event.actor!=='string'||!['WORKER','OBSERVER','OWNER'].includes(event.actor)||
    !EVENTS.includes(event.type))throw Error('INVALID_EVENT');
 const evidence=event.evidenceSha256??null;
 if(evidence!==null&&(typeof evidence!=='string'||!SHA.test(evidence)))throw Error('INVALID_EVIDENCE_HASH');
 if(event.type==='ATTACHMENT_OBSERVED'&&!evidence)throw Error('EVIDENCE_REQUIRED');
 return Object.freeze({
   eventId:event.eventId,transferId:event.transferId,turnId:event.turnId,
   leaseEpoch:event.leaseEpoch,type:event.type,actor:event.actor,evidenceSha256:evidence
 });
};
const nextState=(current,e)=>{
 switch(e.type){
  case 'UPLOAD_STARTED':
   if(e.actor!=='WORKER'||current!=='STAGED')throw Error('ILLEGAL_TRANSITION');
   return 'UNKNOWN'; // persisted BEFORE external action; NEVER auto-replay
  case 'ATTACHMENT_OBSERVED':
   if(e.actor!=='OBSERVER'||current!=='UNKNOWN')throw Error('ILLEGAL_TRANSITION');
   return 'OBSERVED_UNVERIFIED'; // UI evidence is NOT ChatGPT/Telegram delivery proof
  case 'OWNER_RECONCILED':
   if(e.actor!=='OWNER'||!['UNKNOWN','OBSERVED_UNVERIFIED'].includes(current))
     throw Error('ILLEGAL_TRANSITION');
   return 'MANUAL_RECONCILED'; // no transition back to STAGED!
  case 'CANCEL_STAGED':
   if(e.actor!=='OWNER'||current!=='STAGED')throw Error('ILLEGAL_TRANSITION');
   return 'CANCELLED';
  default:throw Error('UNKNOWN_EVENT_TYPE');
 }
};
export function validateTransferSnapshot(transfer){
 allowed(transfer,['schema','transferId','descriptor','state','events','revision',
   'uploadAllowed','uploadPerformed','externalAttachmentVerified','independentChatProof']);
 if(transfer.schema!==SCHEMA||!/^hm1-[0-9a-f]{64}$/.test(transfer.transferId)||
    !PHASES.includes(transfer.state)||!Array.isArray(transfer.events)||
    !Number.isSafeInteger(transfer.revision)||transfer.revision!==transfer.events.length||
    transfer.events.length>8||transfer.uploadAllowed!==false||
    transfer.uploadPerformed!==false||transfer.externalAttachmentVerified!==false||
    transfer.independentChatProof!==false)throw Error('INVALID_TRANSFER_SNAPSHOT');
 const descriptor=validateMediaDescriptor(transfer.descriptor);
 let state='STAGED',seen=new Set();
 for(const raw of transfer.events){
  const e=normalizeEvent(raw);
  if(seen.has(e.eventId)||e.transferId!==transfer.transferId||
     e.turnId!==descriptor.turnId||e.leaseEpoch!==descriptor.leaseEpoch)
    throw Error('CORRUPT_TRANSFER_HISTORY');
  if(e.type==='ATTACHMENT_OBSERVED'&&e.evidenceSha256!==descriptor.sha256)
    throw Error('EVIDENCE_DIGEST_MISMATCH');
  state=nextState(state,e);seen.add(e.eventId);
 }
 if(state!==transfer.state)throw Error('SNAPSHOT_STATE_MISMATCH');
 return true;
}
export function applyMediaEvent(transfer,event){
 validateTransferSnapshot(transfer);
 const e=normalizeEvent(event),d=transfer.descriptor;
 if(e.transferId!==transfer.transferId||e.turnId!==d.turnId||e.leaseEpoch!==d.leaseEpoch)
   throw Error('STALE_OR_FOREIGN_MEDIA_EVENT');
 const prev=transfer.events.find(x=>x.eventId===e.eventId);
 if(prev){
  if(JSON.stringify(prev)!==JSON.stringify(e))throw Error('EVENT_ID_CONFLICT');
  return transfer; // only exact byte-equivalent normalized event is idempotent
 }
 if(e.type==='ATTACHMENT_OBSERVED'&&e.evidenceSha256!==d.sha256)
  throw Error('EVIDENCE_DIGEST_MISMATCH');
 const newState=nextState(transfer.state,e);
 const output=Object.freeze({...transfer,state:newState,revision:transfer.revision+1,
   events:Object.freeze([...transfer.events,e])});
 validateTransferSnapshot(output);
 return output;
}
export function mediaAutomationDecision(transfer){
 validateTransferSnapshot(transfer);
 return Object.freeze({
  uploadAllowed:false,
  retryAllowed:false,
  requiresOwnerReconciliation:['UNKNOWN','OBSERVED_UNVERIFIED'].includes(transfer.state),
  attachmentObservedUnverified:transfer.state==='OBSERVED_UNVERIFIED',
  providerDeliveryVerified:false,
  reason:transfer.state==='STAGED'?'REQUIRES_TRUSTED_LEDGER_AND_APPROVAL':
    transfer.state==='UNKNOWN'?'UNKNOWN_EXTERNAL_SIDE_EFFECT':
    transfer.state==='OBSERVED_UNVERIFIED'?'OBSERVED_NOT_VERIFIED':
    'NO_AUTOMATIC_ACTION'
 });
}
