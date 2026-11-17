import pickle, math, numpy as np, itertools, sys
D="/home/claude/pw_rng/dump"
M=6; K0=8
def rows(year, model, n0, nmax):
    d=pickle.load(open(f"{D}/{year}_{model}.pkl","rb")); sp=d["sigma"]
    out=[]
    for t,s in d["seqs"].items():
        pre=np.array([x[1] for x in s]); at=np.array([x[2] for x in s]); r=at-pre
        for i in range(len(s)-M+1):
            prev=r[:i]
            n=len(prev)
            ss=(prev**2).sum() - (n*prev.mean()**2 if n else 0)
            sig2=(K0*sp*sp + ss)/(K0+max(n-1,0))
            neff=min(n+n0,nmax)
            se2=sig2/neff
            e=at[i:i+M].mean()-pre[i]
            out.append((e, se2, sig2, n))
    return np.array(out)
def score(R,c):
    e,se2,sig2,n=R.T
    sd=np.sqrt(c*c*se2+sig2/M)
    return np.mean(np.abs(e)<=1.96*sd)
def fitc(R):
    lo,hi=0.1,5
    for _ in range(40):
        mid=(lo+hi)/2
        if score(R,mid)<0.95: lo=mid
        else: hi=mid
    return (lo+hi)/2
model=sys.argv[1] if len(sys.argv)>1 else "epa"
best=None
for n0,nmax in itertools.product([1,2,3,5],[6,9,12,20,1000]):
    tr=np.vstack([rows(y,model,n0,nmax) for y in (2022,2023,2024)])
    c=fitc(tr)
    # calibration across n bins: max deviation of coverage from .95
    e,se2,sig2,n=tr.T
    devs=[]
    for a,b in ((0,3),(3,8),(8,15),(15,999)):
        m=(n>=a)&(n<b); devs.append(abs(score(tr[m],c)-0.95))
    sc=max(devs)
    if best is None or sc<best[0]: best=(sc,n0,nmax,c)
    print(n0,nmax,round(c,3),[round(x,3) for x in devs])
print("best",best)
sc,n0,nmax,c=best
for y in (2022,2023,2024,2025,2026):
    R=rows(y,model,n0,nmax); e,se2,sig2,n=R.T
    bins=[]
    for a,b in ((0,3),(3,8),(8,15),(15,999)):
        m=(n>=a)&(n<b); bins.append(round(score(R[m],c),3))
    print(y,"cov",round(score(R,c),3),"by n",bins,"median ±",round(float(np.median(1.96*c*np.sqrt(se2))),2), "late ±", round(float(np.median(1.96*c*np.sqrt(se2[n>=15]))),2), "match sd", round(float(np.median(np.sqrt(sig2))),1))
