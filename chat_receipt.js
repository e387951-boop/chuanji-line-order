/* Copies an accepted server receipt into the originating LINE one-to-one chat. */
(function(root){
 const busy=new Set();
 function available(liff){
  try{return !!(liff&&liff.isInClient()&&liff.getContext()?.type==='utou'&&liff.isApiAvailable('sendMessages'))}catch{return false}
 }
 async function send(o,liff,storage){
  if(o.demo||!o.chat_messages?.length)return 'unavailable';
  const key='cj-chat-receipt:'+o.id;
  if(storage.getItem(key)==='sent')return 'sent';
  if(busy.has(key))return 'pending';
  if(!available(liff))return 'unavailable';
  busy.add(key);
  try{await liff.sendMessages(o.chat_messages);storage.setItem(key,'sent');return 'sent'}
  catch{return 'failed'}finally{busy.delete(key)}
 }
 function mount(o){
  if(o.demo)return;
  const host=document.createElement('section');host.className='form-card';
  host.innerHTML='<h2>官方聊天室訂單紀錄</h2><p role="status"></p><button class="primary full" type="button">傳送訂單明細到官方聊天室</button><p class="help">請從川記官方聊天室的菜單連結開啟。此功能會以你的 LINE 帳號傳送這筆訂單明細。</p><details><summary>查看／複製聊天室明細</summary><textarea readonly rows="10" aria-label="聊天室訂單明細"></textarea></details>';
  document.querySelector('.receipt')?.after(host);
  const status=host.querySelector('[role="status"]'),button=host.querySelector('button');
  host.querySelector('textarea').value=(o.chat_messages||[]).map(m=>m.text).join('\n');
  const key='cj-chat-receipt:'+o.id;
  const render=state=>{
   status.textContent=({sent:'訂單明細已傳送至 LINE 聊天室，店家可在聊天室查看。',pending:'正在傳送，請先不要關閉畫面。',failed:'訂單已成立，但聊天室同步未確認成功。請先查看聊天室，若沒有明細再按下方按鈕重試，請勿重新下單。',unavailable:'訂單已成立，但目前無法自動傳到聊天室。請複製下方明細，回川記官方聊天室貼上並送出。',ready:'可將這筆訂單明細傳送至川記官方聊天室。'})[state];
   button.disabled=state==='sent'||state==='pending';button.hidden=state==='sent'||state==='unavailable';
  };
  const run=async()=>{render('pending');render(await send(o,root.liff,sessionStorage))};
  button.onclick=run;
  if(sessionStorage.getItem(key)==='sent'){render('sent');return}
  if(!available(root.liff)){render('unavailable');return}
  // Only auto-send directly after checkout, never when reopening an old order.
  if(sessionStorage.getItem('cj-chat-auto')===o.id){sessionStorage.removeItem('cj-chat-auto');run()}else render('ready');
 }
 root.ChuanjiChatReceipt={available,send,mount};
 if(typeof module!=='undefined')module.exports={available,send};
})(typeof window!=='undefined'?window:globalThis);
