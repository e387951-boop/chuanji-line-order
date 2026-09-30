"""Safe notification diagnostics: never expose tokens or recipient identifiers."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

def failure_reason(error):
 if isinstance(error,HTTPError):
  code=str(error.code)
  try: message=json.loads(error.read(4096)).get('message','').lower()
  except Exception: message=''
  if code=='429' and 'monthly limit' in message:
   return 'monthly_limit','LINE 本月訊息額度已達上限，請至官方帳號確認方案與額度。'
  return code,{'401':'LINE 存取權杖無效或已過期。','403':'LINE 拒絕此帳號或權限的推播。','400':'LINE 訊息內容或收件人資料不符合規格。','429':'LINE 暫時限制發送頻率，稍後重試。'}.get(code,'LINE 服務暫時無法發送，稍後重試。')
 return 'network','連接 LINE 時發生網路錯誤，稍後重試。'

def quota_status(token):
 result={}
 for name,path in [('quota','quota'),('consumption','quota/consumption')]:
  try:
   request=Request('https://api.line.me/v2/bot/message/'+path,headers={'Authorization':'Bearer '+token})
   with urlopen(request,timeout=8) as response: raw=json.load(response)
   result[name]={k:v for k,v in raw.items() if k in ('type','value','totalUsage')}
  except Exception as error:
   code,message=failure_reason(error); result[name]={'error':message,'code':code}
 return result
