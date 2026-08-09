"""Market-neutral z-score mean reversion Fast Champion discovery."""
import argparse,json,sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.discover_fast_champion import _aggregate,_load  # noqa:E402
from scripts.discover_fast_breakout import _gate,_metrics  # noqa:E402

TFS=('30min','1h'); LOOKBACKS=(24,48,96); ENTRIES=(1.0,1.5,2.0); EXITS=(0.0,.5); STOPS=(2.,3.); HOLDS=(12,24,72); BASE=.14; STRESS=.30

def features(bars,lookback):
 out=bars.copy(); mean=out.close.rolling(lookback).mean(); std=out.close.rolling(lookback).std().replace(0,np.nan); out['z']=(out.close-mean)/std
 prior=out.close.shift(1); tr=pd.concat([out.high-out.low,(out.high-prior).abs(),(out.low-prior).abs()],axis=1).max(axis=1); out['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean(); return out

def run(frame,start,end,entry_z,exit_z,stop_atr,max_hold,cost):
 rows=list(frame[(frame.index>=start)&(frame.index<end)].itertuples()); trades=[]; pos=None; pending=None; pending_exit=None
 def close(ts,price,reason):
  nonlocal pos
  gross=((price/pos['entry_price']-1) if pos['side']=='LONG' else (pos['entry_price']/price-1))*100
  trades.append({**pos,'exit_ts':ts,'exit_price':float(price),'exit_reason':reason,'net_return_pct':gross-cost}); pos=None
 for i,row in enumerate(rows):
  if pending_exit and pos: close(row.Index,row.open,pending_exit)
  pending_exit=None
  if pending and pos is None:
   side,atr=pending; stop=row.open-stop_atr*atr if side=='LONG' else row.open+stop_atr*atr; pos={'side':side,'entry_ts':row.Index,'entry_price':float(row.open),'stop':float(stop),'excursion_id':len(trades)+1}
  pending=None
  if pos:
   stopped=row.low<=pos['stop'] if pos['side']=='LONG' else row.high>=pos['stop']
   if stopped: close(row.Index,pos['stop'],'INITIAL_STOP')
   else:
    mean_exit=row.z>=exit_z if pos['side']=='LONG' else row.z<=-exit_z
    held=(row.Index-pos['entry_ts'])/pd.Timedelta(hours=1)
    if mean_exit or held>=max_hold: pending_exit='MEAN_EXIT' if mean_exit else 'TIMEOUT_EXIT'
  if i+1>=len(rows) or pos or pending_exit or pd.isna(row.z) or pd.isna(row.atr): continue
  if row.z<=-entry_z: pending=('LONG',float(row.atr))
  elif row.z>=entry_z: pending=('SHORT',float(row.atr))
 if pos and rows: close(rows[-1].Index,rows[-1].close,'SEGMENT_END')
 return trades

def main():
 p=argparse.ArgumentParser();p.add_argument('--flow-cache',required=True);p.add_argument('--out',required=True);a=p.parse_args();src=_load(a.flow_cache)
 splits={'train':(pd.Timestamp('2023-08-07'),pd.Timestamp('2025-08-07')),'validation':(pd.Timestamp('2025-08-07'),pd.Timestamp('2026-02-07')),'test':(pd.Timestamp('2026-02-07'),pd.Timestamp('2026-08-07'))}; bars={tf:_aggregate(src,tf) for tf in TFS};grid=[]
 for tf in TFS:
  for lb in LOOKBACKS:
   frame=features(bars[tf],lb)
   for ez in ENTRIES:
    for xz in EXITS:
     for stop in STOPS:
      for hold in HOLDS:
       m=_metrics(run(frame,*splits['train'],ez,xz,stop,hold,BASE),*splits['train'],True);grid.append({'timeframe':tf,'lookback':lb,'entry_z':ez,'exit_z':xz,'stop_atr':stop,'max_hold_hours':hold,'metrics':{'train':m}})
 eligible=[x for x in grid if _gate(x['metrics']['train'],True)];sel=max(eligible,key=lambda x:x['metrics']['train']['net_return_pct']) if eligible else None;stress={}
 if sel:
  f=features(bars[sel['timeframe']],sel['lookback']);par=(sel['entry_z'],sel['exit_z'],sel['stop_atr'],sel['max_hold_hours'])
  for n in ('validation','test'):sel['metrics'][n]=_metrics(run(f,*splits[n],*par,BASE),*splits[n])
  for n,b in splits.items():stress[n]=_metrics(run(f,*b,*par,STRESS),*b)
 passed=bool(sel and all(_gate(sel['metrics'][n]) for n in ('validation','test')) and all(_gate(stress[n]) for n in splits));out={'passed':passed,'grid_size':len(grid),'train_eligible_count':len(eligible),'selected':sel,'cost_stress':stress,'grid':grid};Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps({k:out[k] for k in ('passed','grid_size','train_eligible_count','selected','cost_stress')},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
