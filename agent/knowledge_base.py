"""Small provenance-bearing local KB; reference data never grants permissions."""
from datetime import date
import json
from pathlib import Path
import re


class KnowledgeBase:
    def __init__(self,path):self.path=Path(path)

    def search(self,query,limit=5,public=False):
        if not isinstance(query,str) or not query.strip() or len(query)>200:return []
        if not self.path.exists():return []
        if self.path.stat().st_size>1024*1024:raise ValueError('knowledge base size limit')
        records=json.loads(self.path.read_text(encoding='utf-8')).get('entries',[])
        if not isinstance(records,list):raise ValueError('invalid knowledge base')
        normalized=lambda s:re.sub(r'\s+','',s).casefold()
        whole=normalized(query)
        words=set(re.findall(r'[A-Za-z0-9가-힣_-]{2,}',query.casefold()))
        ranked=[]
        for r in records[:500]:
            if not isinstance(r,dict) or not all(isinstance(r.get(k),str) for k in ('id','title','body','status','verified_at','source')):continue
            if public and r.get('visibility')!='public':continue
            text=normalized(r['title']+' '+r['body']+' '+' '.join(r.get('tags',[])))
            score=20*int(whole in text)+sum(3 for word in words if normalized(word) in text)
            if not score:continue
            try:stale=(date.today()-date.fromisoformat(r['verified_at'][:10])).days>int(r.get('max_age_days',30))
            except (ValueError,TypeError):stale=True
            ranked.append((score,{k:r[k] for k in ('id','title','body','status','verified_at','source')}|{'stale':stale}))
        return [row for _,row in sorted(ranked,key=lambda pair:pair[0],reverse=True)[:max(1,min(int(limit),5))]]
