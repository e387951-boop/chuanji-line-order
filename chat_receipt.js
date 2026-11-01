/* Send only accepted orders, and retain safe diagnostic codes (never tokens). */
(function(root){
 const busy=new Set();
 function inspect(liff){
  let context='none';
  try{
   if(!liff)return {available:false,reason:'sdk_missing',context};
   if(!liff.isInClient())return {available:false,reason:'outside_liff',context:'external'};
   context=liff.getContext()?.type||'none';
   if(context==='none')return {available:false,reason:'context_missing',context};
   if(context!=='utou')return {available:false,reason:'wrong_chat',context};
   if(typeof liff.sendMessages!=='function')return {available:false,reason:'send_missing',context};
   return {available:true,reason:'ready',context};
  }catch{return {available:false,reason:'check_failed',context}}
 }
 function available(liff){return inspect(liff).available}
 async function preflight(liff){
  const result=inspect(liff);
  if(result.available&&liff.permission?.query){
   try{const permission=await liff.permission.query('chat_message.write');if(permission.state==='denied'||permission.state==='prompt')return {...result,available:false,reason:'permission_denied'}}catch{}
  }
  return result;
 }
 async function sendDetailed(o,liff,storage,onResult=()=>{}){
  const environment=inspect(liff),key='cj-chat-receipt:'+o.id;
  const finish=(state,reason,error_code='')=>{const result={state,reason,context:environment.context,error_code};try{onResult(result)}catch{}return result};
  if(o.demo||!o.chat_messages?.length)return finish('unavailable',o.demo?'demo':'messages_missing');
  try{if(storage.getItem(key)==='sent')return finish('sent','storage_sent')}catch{}
  if(busy.has(key))return {state:'pending',reason:'ready',context:environment.context};
  if(!environment.available)return finish('unavailable',environment.reason);
  busy.add(key);
  let timer;
  const operation=(async()=>{
   try{
    // Retry only an explicit rate-limit rejection. Unknown network outcomes may
    // have been delivered already, so they go to the server fallback instead.
    for(let attempt=0;;attempt++){
     try{await liff.sendMessages(o.chat_messages);break}
     catch(e){if(String(e.code)==='429'&&attempt===0){await new Promise(r=>setTimeout(r,1500));continue}throw e}
    }
    try{storage.setItem(key,'sent')}catch{}
    return finish('sent','sent');
   }catch(e){const code=String(e?.code||'UNKNOWN');return finish('failed','api_error',/^[A-Za-z0-9_-]{1,40}$/.test(code)?code:'UNKNOWN')}
   finally{clearTimeout(timer);busy.delete(key)}
  })();
  const timeout=new Promise(resolve=>{timer=setTimeout(()=>resolve(finish('pending','timeout')),15000)});
  return Promise.race([operation,timeout]);
 }
 async function send(o,liff,storage){return (await sendDetailed(o,liff,storage)).state}
 root.ChuanjiChatReceipt={available,inspect,preflight,send,sendDetailed};
 if(typeof module!=='undefined')module.exports=root.ChuanjiChatReceipt;
})(typeof window!=='undefined'?window:globalThis);
