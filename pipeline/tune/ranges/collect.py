"""Run build.py run with sb.features.compute wrapped to save per-team qual sequences."""
import os, sys, pickle, runpy
PIPE = "/home/claude/pitwall/pipeline"
sys.path.insert(0, PIPE)
import sb.features as F
OUT = os.environ["PW_DUMP_DIR"]
orig = F.compute
def wrapped(run, events, config=None):
    feats = orig(run, events, config)
    order = sorted(run.objs[4].values(), key=lambda m: m.time)
    seqs = {}
    for seq, m in enumerate(order):
        if m.elim:
            continue
        for t in list(m.get_red()) + list(m.get_blue()):
            t = int(t)
            if (m.key, t) not in run.updated:
                continue
            at = run.attrib.get(m.key, {}).get(t)
            pre = F._pre_total(run, m, t)
            if at is None or pre is None:
                continue
            seqs.setdefault(t, []).append((seq, float(pre), float(at[0]), m.event))
    with open(os.path.join(OUT, f"{run.year}_{run.model_id}.pkl"), "wb") as f:
        pickle.dump({"seqs": seqs, "sigma": feats.sigma}, f)
    return feats
F.compute = wrapped
sys.argv = ["build.py"] + sys.argv[1:]
runpy.run_path(os.path.join(PIPE, "build.py"), run_name="__main__")
