import json, glob, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rows=[]
def walk(o):
    if isinstance(o,dict):
        v=o.get('mean_net_return_pct')
        if isinstance(v,(int,float)): rows.append(v)
        for x in o.values(): walk(x)
    elif isinstance(o,list):
        for x in o: walk(x)
for f in ['historical_taker_flow_ablation_180d','derivatives_context_ablation_180d','bottom_entry_rules_180d',
          'liquidity_sweep_entry_180d','choch_entry_180d','trailing_breakout_entries_180d',
          'wide_horizon_entries_180d','triple_barrier_entry_model_180d','multivariate_entry_model_180d']:
    try: walk(json.load(open(f'data/backtests/{f}.json')))
    except Exception: pass
g=np.array(rows)+0.30

fig=plt.figure(figsize=(12.8,6.4),dpi=100)
fig.patch.set_facecolor("#0d1117")
ax=fig.add_axes([0.06,0.13,0.88,0.46]); ax.set_facecolor("#0d1117")
ax.hist(np.clip(g,-1.0,1.0),bins=110,color="#58a6ff",edgecolor="none")
ax.axvline(0,color="#8b949e",lw=1.2)
ax.axvline(0.30,color="#f85149",lw=2.4)
ax.text(0.305,ax.get_ylim()[1]*0.72,"  cost you must beat",color="#f85149",fontsize=12,va="center")
ax.set_xlim(-1.0,0.62)
for s in ["top","right","left"]: ax.spines[s].set_visible(False)
ax.spines["bottom"].set_color("#30363d")
ax.set_yticks([]); ax.tick_params(colors="#8b949e",labelsize=10)
ax.set_xlabel("Return per trade, before any fees (%)",color="#8b949e",fontsize=11)

fig.text(0.06,0.855,"29,373 crypto strategies tested.",color="#e6edf3",fontsize=34,fontweight="bold")
fig.text(0.06,0.745,"Zero worked.",color="#f85149",fontsize=34,fontweight="bold")
fig.text(0.06,0.655,"An open research platform that publishes its negative results — and the\nevidence that they are trustworthy.",
         color="#8b949e",fontsize=14.5,linespacing=1.5)
fig.text(0.94,0.028,"github.com/vinhnguyenthanhdn/ai-crypto",color="#6e7681",fontsize=12,ha="right")
fig.savefig("docs/assets/social-preview.png",facecolor="#0d1117")
print("configs:",len(g))
