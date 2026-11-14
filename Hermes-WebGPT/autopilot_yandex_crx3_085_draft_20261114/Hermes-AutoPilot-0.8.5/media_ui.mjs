/** Hermes AutoPilot 0.8.4 media preflight UI.
 * No upload, no save to chrome.storage, no access to Telegram, no network.
 * File is read solely after the owner chooses it. Never log its bytes/name.
 */
import {stageMediaTransfer,MAX_FILE_BYTES,MAX_IMAGE_BYTES,MIME_EXTENSIONS} from './media_contract.mjs';
const allowedExtensions=Object.freeze({
 png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',
 pdf:'application/pdf',txt:'text/plain',csv:'text/csv'
});
const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),
 x=>x.toString(16).padStart(2,'0')).join('');
export function inferMediaType(name,declared=''){
 if(typeof name!=='string'||typeof declared!=='string')throw Error('INVALID_MEDIA_NAME');
 const ext=name.toLowerCase().split('.').at(-1);
 const guessed=Object.hasOwn(allowedExtensions,ext)?allowedExtensions[ext]:null;
 if(!guessed)throw Error('UNSUPPORTED_FILE_EXTENSION');
 if(declared&&declared!==guessed)throw Error('MIME_DECLARATION_MISMATCH');
 return guessed;
}
export function fileSizePreflight(size,mime){
 if(!Number.isSafeInteger(size)||size<1)throw Error('INVALID_FILE_SIZE');
 if(!Object.hasOwn(MIME_EXTENSIONS,mime))throw Error('UNSUPPORTED_MIME');
 const limit=mime.startsWith('image/')?MAX_IMAGE_BYTES:MAX_FILE_BYTES;
 if(size>limit)throw Error('FILE_TOO_LARGE_FOR_INTERNAL_POLICY');
 return limit;
}
export function initMediaUi({talk,getSelectedLease}){
 if(typeof talk!=='function'||typeof getSelectedLease!=='function')throw Error('INVALID_MEDIA_UI_DEPS');
 const fileInput=document.querySelector('#media-file');
 const previewButton=document.querySelector('#media-preview');
 const output=document.querySelector('#media-preview-status');
 if(!fileInput||!previewButton||!output)throw Error('MEDIA_UI_CONTROLS_MISSING');
 let attachmentId=null;
 fileInput.addEventListener('change',()=>{
  attachmentId=crypto.randomUUID().replaceAll('-','');
  output.textContent='Файл выбран. Нажми «Проверить» — данные не будут отправлены.';
 });
 previewButton.addEventListener('click',async()=>{
  if(previewButton.disabled)return;
  previewButton.disabled=true;
  try{
   const file=fileInput.files?.[0];
   if(!file||fileInput.files.length!==1)throw Error('SELECT_EXACTLY_ONE_FILE');
   const lease=getSelectedLease();
   if(!lease)throw Error('SELECT_WORKING_TASK_FIRST');
   // Before touching file bytes, ask the service worker to validate that the
   // selected task's lease is still current. This is an owner-visible preview.
   const binding=await talk({type:'HANDOFF_PREVIEW',...lease});
   if(binding?.state!=='PREVIEW_ONLY'||binding?.relayBound!==false||
      binding?.sendEnabled!==false||binding?.schema!=='autopilot-relay-turn-preview-v1')
     throw Error('UNTRUSTED_TURN_PREVIEW');
   const mimeType=inferMediaType(file.name,file.type);
   fileSizePreflight(file.size,mimeType);
   if(!attachmentId)attachmentId=crypto.randomUUID().replaceAll('-','');
   output.textContent='Проверяю выбранный файл локально. Сеть не используется…';
   const bytes=await file.arrayBuffer();
   const descriptor={
    turnId:binding.turnId,attachmentId,direction:'TELEGRAM_TO_CHATGPT',
    fileName:file.name,mimeType,sizeBytes:file.size,
    sha256:await sha(bytes),leaseEpoch:binding.epoch
   };
   const result=await stageMediaTransfer(descriptor,bytes);
   if(result.state!=='STAGED'||result.uploadAllowed!==false||
      result.externalAttachmentVerified!==false||result.independentChatProof!==false)
      throw Error('UNSAFE_MEDIA_PREVIEW_RESULT');
   output.textContent=
      'Проверено локально: '+result.descriptor.mimeType+', '+result.descriptor.sizeBytes+' байт.\n'+
      'SHA-256: '+result.descriptor.sha256+'\n'+
      'ID вложения: '+result.transferId+'\n'+
      'Статус: STAGED (только предварительная проверка).\n'+
      'Файл не загружен в ChatGPT, не получен из Telegram и не сохранён расширением.';
   // No network, no upload, no chrome.storage write: result is ephemeral.
  }catch(error){
   output.textContent='Подготовка отклонена: '+String(error?.message||'UNKNOWN_ERROR');
  }finally{previewButton.disabled=false;}
 });
}
