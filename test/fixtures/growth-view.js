// Explicitly synthetic Web UI fixture. Never read an owner's profile or keys.
export const growthFixture = () => ({version:1, level:12,xp:4900,into:60,need:920,ratio:60/920,
  stage:{key:'fish',name:'御风'},streak:4,
  counters:{events:80,turns:18,okTurns:15,errorTurns:2,toolCalls:64,toolErrors:4,recoveries:2,approvals:3},
  traits:{stamina:42,wit:66,affinity:58,calm:88},
  learned:[{id:'tool',text:'最常用的工具是 read（24 次）',at:'2026-10-01T08:00:00Z'},
    {id:'hour',text:'你最常在 14 点活跃',at:'2026-10-02T08:00:00Z'},
    {id:'streak',text:'已经连续 4 天陪你工作',at:'2026-10-02T08:00:00Z'}],
  diary:[{kind:'review',text:'合成演示：一起完成一次检查，记下这段安静的同行。',at:'2026-10-02T08:00:00Z',level:12},
    {kind:'stage',text:'合成演示：进入御风阶段，能自己穿过云层。',at:'2026-10-01T08:00:00Z',level:10}],
  createdAt:'2026-09-01T00:00:00Z',updatedAt:'2026-10-02T08:00:00Z',
  storageHealth:{ok:true,readable:true,writable:true},
})
