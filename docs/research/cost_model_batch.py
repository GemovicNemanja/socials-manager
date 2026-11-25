# Batch-mode cost model: the platform runs every G days instead of continuously.
import math
C=0.00001; T_TWEET=15*C; T_BATCH100=1*C
X_POST, X_USER, X_OWNED = 0.005, 0.010, 0.001
X_ANALYTICS = 0.005   # ASSUMPTION (price not listed)
DAYS=30; PPD=3; POSTS=PPD*DAYS
R,Q,NEWF=10,2,30      # per post replies, quotes; new followers/day at 1k followers (as in cost_model.py)

def rechecks(G, target_days=7.0):
    # a post first seen at age a ~ U[0,G) is re-read at a+G, a+2G, ... until age >= target
    n=2000; s=0
    for i in range(n):
        a=(i+0.5)/n*G; k=0
        while a < target_days: a+=G; k+=1
        s+=k
    return s/n

def owner_official(G):
    runs=DAYS/G
    return {
      'timeline metrics, posts <30d, each run (owned)': 3*30*X_OWNED*runs,
      '/2/tweets/analytics, 2 pulls/post (assumed price)': POSTS*2*X_ANALYTICS,
      'follower count per run (user read)': X_USER*runs,
    }
def owner_extras_tapi(G):
    runs=DAYS/G
    return {
      'new followers since last run (200/page)': runs*max(1,math.ceil(NEWF*G/200))*200*C,
      'unfollower diff (followers_ids, 1k ids)': runs*1000*0.45*C,
      'replies+quotes since last run': POSTS*(R+Q)*T_TWEET,
    }
def owner_extras_official(G):
    runs=DAYS/G
    return {
      'new followers/unfollowers (owned follower list)': runs*max(100,1000)*X_OWNED,
      'replies (owned mentions)': POSTS*R*X_OWNED,
      'quotes (post reads)': POSTS*Q*X_POST,
    }
def peer_tapi(G, n_peers=100):
    runs=DAYS/G; rc=rechecks(G)
    return {
      'discovery: grouped since_time search': POSTS*T_TWEET,
      'rechecks by id until age >= 7d': POSTS*rc*T_TWEET,
      'follower count per run (batch>=100)': runs*T_BATCH100,
    }
def peer_official(G):
    runs=DAYS/G; rc=rechecks(G)
    return {
      'discovery + rechecks (post reads)': POSTS*(1+rc)*X_POST,
      'follower count per run (user read)': runs*X_USER,
    }
tot=lambda d: sum(d.values())
for G in (2,3,4,5):
    oo,ot,oe=tot(owner_official(G)),tot(owner_extras_tapi(G)),tot(owner_extras_official(G))
    pt,po=tot(peer_tapi(G)),tot(peer_official(G))
    print(f'G={G}d rechecks/post={rechecks(G):.2f} | owner official ${oo:.2f} (+analytics incl.) | owner extras tapi ${ot:.2f} / official ${oe:.2f} | per peer tapi ${pt:.4f} official ${po:.3f} | 100 peers tapi ${100*pt:.2f} official ${100*po:.0f} | 50 peers official ${50*po:.0f}')
G=3
llm=3.0; research=13.0
hy=tot(owner_official(G))+tot(owner_extras_tapi(G))+100*tot(peer_tapi(G))
off=tot(owner_official(G))+tot(owner_extras_official(G))+100*tot(peer_official(G))
print(f'\nG=3 workspace without on-demand research: hybrid ${hy+llm:.2f}, official-only ${off+llm:.2f}')
print(f'G=3 workspace with on-demand research allowance: hybrid ${hy+llm+research:.2f}, official-only ${off+llm+435:.2f} (official research $435)')
print('backfill 200x200 tapi', 200*200*T_TWEET, 'official', 200*200*X_POST, '| 100x200 official', 100*200*X_POST)
