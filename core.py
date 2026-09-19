"""Chuanji ordering rules. No AI or external packages required."""
from datetime import datetime, timedelta, timezone
import copy
import re
import secrets

TZ = timezone(timedelta(hours=8))
MENU = [
    ('四喜麵線糊', 110, 95, '大腸、蚵仔、蝦仁、發魷魚'),
    ('經典麵線糊', 95, 80, '大腸、蚵仔、虱目魚漿'),
    ('大腸麵線糊', 85, 70, '大腸、虱目魚漿、貢丸'),
    ('蚵仔麵線糊', 85, 70, '蚵仔、虱目魚漿、貢丸'),
    ('蝦仁麵線糊', 85, 70, '蝦仁、虱目魚漿、貢丸'),
    ('魷魚麵線糊', 85, 70, '發魷魚、虱目魚漿、貢丸'),
    ('清麵線糊', 50, 40, '油條、麵線糊'),
]
SIDES = [('滷板豆腐（4塊）',20),('滷米血（6塊）',20),('滷豆乾（6塊）',20),('滷貢丸（3顆）',15),('滷蛋（1顆）',15)]
EXTRAS = [('大腸',20),('鮮蚵',20),('蝦仁',20),('魷魚',20),('虱目魚漿',15),('油條',15),('貢丸',15)]
OMIT = ['胡椒粉','烏醋','香油','蒜泥','香菜','大腸','蚵仔','蝦仁','發魷魚','虱目魚漿','貢丸','油條']
SPICY = ['不辣','微辣','小辣','中辣','大辣']

def fresh():
    return {'stage':'menu','cart':[], 'rev':secrets.token_hex(5)}

def amount(item):
    if item['kind'] == 'side':
        return SIDES[item['id']][1] * item['qty']
    base = MENU[item['id']][1 if item['size']=='大碗' else 2]
    return (base + sum(EXTRAS[int(k)][1]*v for k,v in item['extras'].items())) * item['qty']

def description(item):
    if item['kind']=='side':
        return f"{SIDES[item['id']][0]} × {item['qty']}份｜${amount(item)}"
    extra = '、'.join(f'{EXTRAS[int(k)][0]}×{v}' for k,v in item['extras'].items()) or '無'
    omit = '、'.join(item['omit']) or '無'
    return f"{MENU[item['id']][0]} {item['size']} × {item['qty']}碗｜${amount(item)}\n每碗加料：{extra}\n辣度：{item['spicy']}；不加：{omit}" + ('；香菜換九層塔' if item['basil'] else '')

def summary(s):
    parts = [f'{n+1}. {description(i)}' for n,i in enumerate(s['cart'])]
    parts += [f"總計 NT$ {sum(amount(i) for i in s['cart'])}"]
    if s.get('phone'): parts.append('電話：'+s['phone'])
    if s.get('pickup'): parts.append('取餐：'+s['pickup'])
    if s.get('note'): parts.append('備註：'+s['note'])
    return '\n\n'.join(parts)

def pickup_value(value, now=None):
    now = now or datetime.now(TZ)
    value = value.strip().replace('T',' ')
    for word, offset in [('今天',0),('明天',1),('後天',2)]:
        if value.startswith(word):
            value = (now+timedelta(days=offset)).strftime('%Y-%m-%d')+' '+value[len(word):].strip()
            break
    try: dt = datetime.strptime(value,'%Y-%m-%d %H:%M').replace(tzinfo=TZ)
    except ValueError: raise ValueError('請輸入「2026-09-20 12:30」這樣的日期時間，也可輸入「明天 12:30」。')
    if dt.weekday()==5: raise ValueError('週六公休，請選擇其他日期。')
    if not 11 <= dt.hour < 23: raise ValueError('取餐時段為 11:00 至 22:59，23:00 打烊。')
    if dt <= now: raise ValueError('取餐時間已過，請選擇未來的時間。')
    return dt.strftime('%Y-%m-%d %H:%M')

def view(s, notice=''):
    stage=s['stage']; options=[]; text=''; cards=[]
    def option(label, cmd): options.append({'label':label,'cmd':cmd})
    if stage=='menu':
        text='川記麵線糊｜暖暖一碗，現點現做\n自取 11:00–23:00・週六公休\n請選擇餐點。不同口味請分次加入。'
        for k,m in enumerate(MENU): cards.append({'title':m[0],'detail':m[3], 'price':f'小 ${m[2]} / 大 ${m[1]}','cmd':f'food:{k}'})
        option('加點滷味','sides'); option(f"購物車（{sum(i['qty'] for i in s['cart'])}）",'cart')
    elif stage=='size':
        m=MENU[s['draft']['id']]; text=f'{m[0]}\n原有配料：{m[3]}\n請選大小碗'
        option(f'大碗 ${m[1]}','size:大碗'); option(f'小碗 ${m[2]}','size:小碗')
    elif stage=='qty':
        text='這個口味要幾份？同一筆餐點會使用相同加料與調味。'
        for n in range(1,7): option(str(n)+'份','qty:'+str(n))
    elif stage=='extras':
        d=s['draft']; text='加料（每碗）\n可重複點選增加份數；不加請按「下一步」。\n目前：'+('、'.join(f'{EXTRAS[int(k)][0]}×{v}' for k,v in d['extras'].items()) or '無')
        for n,e in enumerate(EXTRAS): option(f'{e[0]} +${e[1]}',f'extra:{n}')
        option('清除加料','extra:clear'); option('下一步','spice')
    elif stage=='spice':
        text='請選辣度。預設調味：胡椒粉、烏醋、香油、蒜泥、香菜。'
        for p in SPICY: option(p,'spicy:'+p)
    elif stage=='season':
        d=s['draft']; text='調味與不吃的配料\n不加：'+('、'.join(d['omit']) or '無')+'\n香菜換九層塔：'+('是' if d['basil'] else '否')
        option('香菜換九層塔','basil'); option('選擇不加項目','omit'); option('加入購物車','add')
    elif stage=='omit':
        d=s['draft']; text='點選切換「不加」項目，再按完成。\n已選：'+('、'.join(d['omit']) or '無')
        for name in OMIT: option(('✓ ' if name in d['omit'] else '')+name,'omit:'+name)
        option('完成','season')
    elif stage=='sides':
        text='加點滷味｜份量固定，可選份數'
        for n,m in enumerate(SIDES): option(f'{m[0]} ${m[1]}',f'side:{n}')
        option('返回菜單','menu'); option('購物車','cart')
    elif stage=='cart':
        text=summary(s) if s['cart'] else '購物車還是空的，先選一碗麵線吧。'
        option('繼續選餐','menu'); option('加點滷味','sides')
        if s['cart']: option('移除餐點','remove'); option('前往確認','checkout')
        option('清空購物車','clear')
    elif stage=='clear':
        text='確定清空尚未送出的購物車？'; option('確定清空','clear:yes'); option('保留餐點','cart')
    elif stage=='remove':
        text='選擇要移除的餐點；要修改口味，可以先移除再重新加入。'
        for n,i in enumerate(s['cart']): option(f'{n+1}. '+(MENU[i['id']][0] if i['kind']=='food' else SIDES[i['id']][0]),f'remove:{n}')
        option('返回購物車','cart')
    elif stage=='phone': text='請輸入取餐聯絡電話（台灣手機或市話）。\n電話僅用於本次訂單聯繫。'
    elif stage=='pickup': text='請輸入取餐日期與時間，例如「明天 12:30」。\n自取・週六公休・11:00–22:59。'
    elif stage=='note':
        text='有其他備註嗎？如有特殊需求，須由店家確認。'; option('沒有備註','note:none')
    elif stage=='review':
        text='請確認訂單\n\n'+summary(s)+'\n\n付款：尚未付款（線上付款尚未開放）\n送出後需等候店家確認，並非保證取餐時間。'
        option('送出訂單','confirm'); option('修改餐點','cart'); option('修改電話時間','checkout')
    elif stage=='done':
        text='訂單已送出，等待店家確認\n訂單編號：'+s['order_id']+'\n\n'+summary(s)+'\n\n尚未付款。修改或取消已送出訂單，請聯繫店家。'
        option('再點一筆','new')
    if stage not in ('menu','cart','review','done','clear','remove','sides'):
        option('返回購物車','cart')
    return {'text':(notice+'\n\n' if notice else '')+text,'options':options[:13],'cards':cards,'stage':stage,'rev':s['rev'],'total':sum(amount(i) for i in s['cart']),'cart':s['cart'],'summary':summary(s)}

def step(state, command='', value='', revision=None, now=None):
    s=copy.deepcopy(state); stage=s['stage']; order=None; notice=''
    if revision is not None and revision!=s['rev']: return s,view(s,'這是較早的按鈕，請使用最新選項。'),None
    try:
        allowed={o['cmd'] for o in view(s)['options']} | {c['cmd'] for c in view(s)['cards']}
        if command and command not in allowed and command not in ('start',): raise ValueError('請使用目前畫面的選項。')
        if command=='start':
            return s,view(s),None
        if command in ('menu','cart','sides','spice','season','omit','remove','clear'): s['stage']=command
        elif command=='clear:yes' or command=='new': s=fresh()
        elif command.startswith('food:'):
            s['draft']={'kind':'food','id':int(command.split(':')[1]),'size':'','qty':1,'extras':{},'spicy':'不辣','omit':[],'basil':False}; s['stage']='size'
        elif command.startswith('side:'):
            s['draft']={'kind':'side','id':int(command.split(':')[1]),'qty':1}; s['stage']='qty'
        elif command.startswith('size:'): s['draft']['size']=command.split(':')[1]; s['stage']='qty'
        elif command.startswith('qty:'):
            s['draft']['qty']=int(command.split(':')[1])
            if s['draft']['kind']=='side':
                if len(s['cart'])>=10: raise ValueError('每筆最多10種餐點，請先送出這筆訂單。')
                s['cart'].append(s.pop('draft')); s['stage']='cart'
            else: s['stage']='extras'
        elif command.startswith('extra:'):
            k=command.split(':')[1]; ex=s['draft']['extras']
            if k=='clear': ex.clear()
            else:
                if sum(ex.values())>=6: raise ValueError('每碗加料最多6份；如需更多請備註並由店家確認。')
                ex[k]=ex.get(k,0)+1
        elif command.startswith('spicy:'): s['draft']['spicy']=command.split(':')[1]; s['stage']='season'
        elif command=='basil':
            d=s['draft']; d['basil']=not d['basil']
            if d['basil'] and '香菜' not in d['omit']: d['omit'].append('香菜')
            elif not d['basil'] and '香菜' in d['omit']: d['omit'].remove('香菜')
        elif command.startswith('omit:'):
            name=command.split(':')[1]; d=s['draft']
            if name in d['omit']: d['omit'].remove(name)
            else: d['omit'].append(name)
            if name=='香菜' and name not in d['omit']: d['basil']=False
        elif command=='add':
            if len(s['cart'])>=10: raise ValueError('每筆最多10種餐點，請先送出這筆訂單。')
            s['cart'].append(s.pop('draft')); s['stage']='cart'
        elif command.startswith('remove:'): s['cart'].pop(int(command.split(':')[1])); s['stage']='cart'
        elif command=='checkout': s['stage']='phone'
        elif stage=='phone' and not command:
            phone=re.sub(r'[\s()-]','',value)
            if not re.fullmatch(r'(09\d{8}|0[2-8]\d{7,8})',phone): raise ValueError('請輸入完整台灣聯絡電話，例如 0912345678。')
            s['phone']=phone; s['stage']='pickup'
        elif stage=='pickup' and not command: s['pickup']=pickup_value(value,now); s['stage']='note'
        elif stage=='note':
            if len(value)>120: raise ValueError('備註請控制在120字內。')
            s['note']='' if command=='note:none' else value.strip(); s['stage']='review'
        elif command=='confirm':
            pickup_value(s['pickup'],now)
            s['order_id']='CJ'+(now or datetime.now(TZ)).strftime('%m%d')+'-'+secrets.token_hex(4).upper()
            s['stage']='done'; order={'id':s['order_id'],'total':sum(amount(i) for i in s['cart']),'payment':'unpaid','status':'pending','detail':copy.deepcopy(s)}
        elif value:
            raise ValueError('請點選下方按鈕選餐；這個版本使用按鈕流程，不會自動猜測文字訂單。')
        s['rev']=secrets.token_hex(5)
    except (ValueError,KeyError,IndexError) as e:
        return state,view(state,str(e)),None
    return s,view(s,notice),order
