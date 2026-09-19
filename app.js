let state, busy=false;
const chat=document.querySelector('#chat'), choices=document.querySelector('#choices'), input=document.querySelector('#text');
function element(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function bubble(text,user=false){chat.append(element('div',text,'bubble'+(user?' user':'')))}
async function send(cmd='',value='',label=''){
 if(busy)return; busy=true; document.querySelectorAll('button').forEach(b=>b.disabled=true);
 if(label||value)bubble(label||value,true);
 try{
  const response=await fetch('/demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd,value,rev:state?.rev})});
  if(!response.ok)throw Error();state=await response.json(); render();
 }catch(e){bubble('暫時無法連線，請確認預覽服務仍在執行，再重試。');}
 finally{busy=false;document.querySelectorAll('#choices button,#send').forEach(b=>b.disabled=false);}
}
function render(){
 // Older cards deliberately stay disabled to prevent double taps or stale ordering actions.
 bubble(state.text);
 if(state.cards.length){const strip=element('div',undefined,'menu-cards');state.cards.forEach(c=>{const card=element('article',undefined,'menu-card'),art=element('div',undefined,'food-art');art.append(element('div',undefined,'bowl'));card.append(art,element('h3',c.title),element('p',c.detail),element('div',c.price,'price'));const b=element('button','選這碗');b.onclick=()=>send(c.cmd,'',c.title);card.append(b);strip.append(card)});chat.append(strip)}
 choices.replaceChildren();state.options.forEach(o=>{const b=element('button',o.label);b.onclick=()=>send(o.cmd,'',o.label);choices.append(b)});
 input.disabled=!['phone','pickup','note'].includes(state.stage);input.type=state.stage==='phone'?'tel':state.stage==='pickup'?'datetime-local':'text';input.value='';input.placeholder=state.stage==='phone'?'輸入聯絡電話':state.stage==='note'?'輸入備註，或按「沒有備註」':'請使用選餐按鈕';
 document.querySelector('#send').style.display=input.disabled?'none':'block';
 const cart=document.querySelector('#cart');cart.replaceChildren();
 if(!state.cart.length)cart.append(element('div','餐點選好後\n會出現在這裡。','empty'));
 else{state.summary.split('\n\n').slice(0,state.cart.length).forEach(t=>cart.append(element('div',t,'cart-item')))}
 document.querySelector('#total').textContent='NT$ '+state.total;
 const orders=document.querySelector('#orders');orders.replaceChildren();document.querySelector('#no-orders').hidden=!!state.orders.length;
 state.orders.forEach(o=>{const n=element('div',undefined,'notification');n.append(element('b','測試訂單 '+o.id),element('div','尚未付款・等待店家確認\n\n'+o.summary));orders.append(n)});
 chat.scrollTop=chat.scrollHeight;
}
document.querySelector('#form').onsubmit=e=>{e.preventDefault();if(!input.disabled&&input.value.trim())send('',input.value.trim())};send('start');
