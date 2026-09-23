(function(root){
 function toCart(order){
  if(!Array.isArray(order.items)||!order.items.length)throw Error('找不到上次餐點');
  return order.items.map(i=>({product_id:i.product_id,size:i.size,qty:i.qty||1,spicy:i.spicy||'不辣',omit:[...(i.omit||[])],basil:!!i.basil,note:i.note||'',extras:Object.fromEntries((i.extras||[]).map(a=>[a.id,a.qty]))}));
 }
 function key(id){return id?'cj-returning-v1:'+id:null}
 function load(storage,id){try{const value=JSON.parse(storage.getItem(key(id)));return id&&value&&Array.isArray(value.items)?value:null}catch{return null}}
 function save(storage,id,o){if(!id||o.demo)return;try{storage.setItem(key(id),JSON.stringify({id:o.id,pickup_number:o.pickup_number,created_at:o.created_at,name:o.name,phone:o.phone,items:o.items,total:o.total,utensils:o.utensils,note:o.note||''}))}catch{}}
 root.ChuanjiReturning={toCart,load,save};
 if(typeof module!=='undefined')module.exports=root.ChuanjiReturning;
})(typeof window!=='undefined'?window:globalThis);
