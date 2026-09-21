/* Copies an accepted server receipt into the originating LINE one-to-one chat. */
(function(root){
 const busy=new Set();
 function available(liff){
  try{return !!(liff&&liff.isInClient()&&liff.getContext()?.type==='utou'&&typeof liff.sendMessages==='function')}catch{return false}
 }
 async function send(o,liff,storage){
  if(o.demo||!o.chat_messages?.length)return 'unavailable';
  const key='cj-chat-receipt:'+o.id;
  try{if(storage.getItem(key)==='sent')return 'sent'}catch{}
  if(busy.has(key))return 'pending';
  if(!available(liff))return 'unavailable';
  busy.add(key);
  try{await liff.sendMessages(o.chat_messages);try{storage.setItem(key,'sent')}catch{}return 'sent'}
  catch{return 'failed'}finally{busy.delete(key)}
 }
 root.ChuanjiChatReceipt={available,send};
 if(typeof module!=='undefined')module.exports={available,send};
})(typeof window!=='undefined'?window:globalThis);
