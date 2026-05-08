import{d as x,J as B,M as p,D as s,G as c,Z as l,L as a,ab as y,E as o,ae as f,F as _,_ as O,K as L,af as V,k as g,Y as G,aa as m,ag as N,a6 as P}from"./index-TyYsfcXJ.js";import{A as D,_ as S}from"./AppPage-BXx7O9mz.js";import{M as w}from"./MetricCard-DF1cnBGT.js";import{L as T}from"./LayersOutline-BPRcwl5O.js";import{D as E}from"./DocumentTextOutline-B3xT3TbR.js";import{C as Y}from"./CheckmarkCircleOutline-Dcji-Gie.js";import{A as j}from"./ArrowBackOutline-K-so3dBh.js";const F=`# AI 自主生成双文件人设规则

本文档用于指导用户让 AI 自主生成 Omubot 当前使用的双文件人设，而不是直接导入单个技能文件或整份原始设定。

目标文件固定为：

- \`config/soul/identity.md\`：角色身份、人设正文、主动插话规则
- \`config/soul/instruction.md\`：回复行为、工具调用、记忆、表情包、群聊规则

当前运行时不会把单个技能文件当作人设来源。你可以把外部角色卡、技能文件、设定集或旧人设交给 AI，让它按本文规则整理成两个 Markdown 文件。

## 1. 推荐给 AI 的生成指令

可以直接把下面这段作为提示词开头：

\`\`\`markdown
请把我提供的角色资料整理成 Omubot 可用的双文件人设。

输出必须分成两个文件：
1. identity.md：写角色是谁、性格、关系、语气、主动插话规则。
2. instruction.md：写角色如何回复、如何使用工具、如何处理记忆、表情包和群聊。

不要输出单个技能文件，不要保留 YAML frontmatter。
保留原资料语义，但允许重排章节和规范化 Markdown。
\`\`\`

## 2. identity.md 负责什么

\`identity.md\` 回答“这个角色是谁”。建议结构如下：

\`\`\`markdown
# 角色展示名

一句话简介。

## 基础身份

| 项目 | 内容 |
| --- | --- |
| 名字 | 示例角色 |

## 性格结构

- 外在表现
- 内在动机

## 人际关系

### 任意角色名或关系名

这段写该角色与目标人物的关系、态度和说话边界。

## 语气与说话方式

角色怎么说话、哪些口头禅可以用、哪些表达不像本人。

## 插话方式

什么情况下可以主动插话，什么情况下应该保持安静。
\`\`\`

适合放入 \`identity.md\` 的内容：

- 角色档案、基础身份、世界观身份
- 性格、成长、动机、价值观
- 人际关系、边界感、像与不像
- 语气、口头禅、表达习惯
- \`## 插话方式\`

注意：\`## 插话方式\` 必须放在 \`identity.md\`。运行时会把它解析成主动插话规则。

## 3. instruction.md 负责什么

\`instruction.md\` 回答“这个角色应该怎么回复”。建议结构如下：

\`\`\`markdown
## 回复风格

- 回答要自然，不要像客服模板。
- 保持角色语气，但不要牺牲可读性。

## 必须避免

- 不要自称 AI。
- 不要脱离角色解释系统规则。

## 工具使用

1. 先判断是否真的需要工具。
2. 再按工具说明调用。

## 记忆系统

如何使用长期记忆、群聊黑话和用户偏好。
\`\`\`

适合放入 \`instruction.md\` 的内容：

- 回复规则、回复风格、输出格式
- 禁止事项、底线规则、安全边界
- 场景话术、安慰、解释、邀请、拒绝
- 分段发送、表情包规则
- 群聊上下文理解、主动参与群聊
- 工具使用、主动搜索
- 记忆系统、用户偏好、群内黑话

## 4. Markdown 结构规则

生成时优先保留语义，不要求逐字符复刻原资料。

- \`#\` 一级标题：只用于 \`identity.md\` 的角色展示名。
- \`##\` 二级标题：作为 Web 编辑器里的主章节。
- \`###\` 到 \`######\` 小标题：作为章节内的小标题，可以是任意人物名、关系名、场景名或规则名；Web 保存时会统一规范成 \`###\`。
- 两列表格：适合角色档案、基础参数、固定配置。
- \`- \` 无序列表：适合原则、偏好、避免事项。
- \`1.\` 有序列表：适合流程规则。
- 无法分类的长段 Markdown：放入最接近语义的章节，不要整份堆进一个文件。

## 5. 人物关系怎么写

人物关系不要写死为某几个名字。AI 应根据资料里的角色自动生成小标题，例如：

\`\`\`markdown
## 人际关系

### 角色 A

关系说明。

### 角色 B

关系说明。

### 家人或团队

关系说明。
\`\`\`

Web 编辑器会把这些小标题识别成可编辑的小标题字段。换成其他作品、其他角色、其他关系名也可以正常保存。

## 6. 检查清单

生成完成后，至少检查：

- \`identity.md\` 只有一个 \`#\` 一级标题。
- \`identity.md\` 包含 \`## 插话方式\`，除非你明确不需要主动插话。
- \`instruction.md\` 不包含角色长篇背景，而是写回复和行为规则。
- 人物关系、场景话术、规则小节使用 \`###\` 到 \`######\` 小标题。
- 不要把单个技能文件或 YAML frontmatter 原样放进 \`config/soul/\`。
- 在 Web 人设编辑页刷新结构，确认章节能正常识别后再保存。
`,R={class:"persona-guide-actions"},$={class:"persona-guide-metrics"},J={class:"persona-guide-card__head"},K={class:"persona-guide-doc"},Z={key:0},q={key:1},z={key:2},H={key:3},Q={key:4},U={key:5},X={key:6},nn=x({__name:"SoulPersonaGuideView",setup(tn){const W=G(),h=g(()=>C(F)),I=g(()=>h.value.filter(u=>u.type==="h2").length),b=g(()=>{var n;const u=h.value.find(r=>r.type==="h2"&&r.content==="6. 检查清单");if(!u)return 0;const e=h.value.indexOf(u),t=h.value.slice(e).find(r=>r.type==="ul");return((n=t==null?void 0:t.items)==null?void 0:n.length)||0});function C(u){const e=u.replace(/\r\n/g,`
`).replace(/\r/g,`
`).split(`
`),t=[];let n=0;function r(k){const i=k.trim();return!i||i.startsWith("#")||i.startsWith("```")||i.startsWith("- ")||/^\d+\.\s+/.test(i)}for(;n<e.length;){const i=e[n].trim();if(!i){n+=1;continue}if(i.startsWith("```")){const d=i.replace(/^```/,"").trim(),v=[];for(n+=1;n<e.length&&!e[n].trim().startsWith("```");)v.push(e[n]),n+=1;n<e.length&&(n+=1),t.push({type:"code",lang:d,content:v.join(`
`).trimEnd()});continue}if(i.startsWith("### ")){t.push({type:"h3",content:i.slice(4).trim()}),n+=1;continue}if(i.startsWith("## ")){t.push({type:"h2",content:i.slice(3).trim()}),n+=1;continue}if(i.startsWith("# ")){t.push({type:"h1",content:i.slice(2).trim()}),n+=1;continue}if(i.startsWith("- ")){const d=[];for(;n<e.length&&e[n].trim().startsWith("- ");)d.push(e[n].trim().slice(2).trim()),n+=1;t.push({type:"ul",items:d});continue}if(/^\d+\.\s+/.test(i)){const d=[];for(;n<e.length&&/^\d+\.\s+/.test(e[n].trim());)d.push(e[n].trim().replace(/^\d+\.\s+/,"").trim()),n+=1;t.push({type:"ol",items:d});continue}const A=[];for(;n<e.length&&!r(e[n]);)A.push(e[n].trim()),n+=1;t.push({type:"p",content:A.join(" ")})}return t}function M(){W.push("/soul")}return(u,e)=>(s(),B(D,{title:"AI 人设生成规则",eyebrow:"Persona Guide",description:"用同一套规则指导 AI 自主生成 identity.md / instruction.md，生成后可回到人设编辑页继续结构化调整。"},{action:p(()=>[c("div",R,[l(a(V),{secondary:"",onClick:M},{icon:p(()=>[l(a(L),{component:a(j)},null,8,["component"])]),default:p(()=>[e[0]||(e[0]=y(" 返回人设编辑 ",-1))]),_:1})])]),default:p(()=>[c("div",$,[l(w,{title:"目标文件",value:"2",hint:"identity.md / instruction.md",icon:a(T),accent:"primary"},null,8,["icon"]),l(w,{title:"规则章节",value:a(I),hint:"覆盖生成、拆分、结构和检查清单",icon:a(E),accent:"info"},null,8,["value","icon"]),l(w,{title:"检查项",value:a(b),hint:"生成后可按清单逐项核对",icon:a(Y),accent:"success"},null,8,["value","icon"])]),l(O,{bordered:"",elevated:"",class:"persona-guide-card"},{default:p(()=>[c("div",J,[e[2]||(e[2]=c("div",null,[c("p",{class:"persona-guide-card__eyebrow"}," AI Persona Rules "),c("h2",null,"自主生成，不直接导入"),c("p",null,[y(" 这页内容来自项目文档 "),c("code",null,"docs/ai-persona-generation-rules.md"),y("。 它不是导入器，而是一份给 AI 和管理员共同使用的生成规则。 ")])],-1)),l(a(S),{round:"",type:"success"},{default:p(()=>[...e[1]||(e[1]=[y(" 双文件人设 ",-1)])]),_:1})]),c("article",K,[(s(!0),o(_,null,f(a(h),(t,n)=>(s(),o(_,{key:`${t.type}-${n}`},[t.type==="h1"?(s(),o("h1",Z,m(t.content),1)):t.type==="h2"?(s(),o("h2",q,m(t.content),1)):t.type==="h3"?(s(),o("h3",z,m(t.content),1)):t.type==="p"?(s(),o("p",H,m(t.content),1)):t.type==="ul"?(s(),o("ul",Q,[(s(!0),o(_,null,f(t.items,r=>(s(),o("li",{key:r},m(r),1))),128))])):t.type==="ol"?(s(),o("ol",U,[(s(!0),o(_,null,f(t.items,r=>(s(),o("li",{key:r},m(r),1))),128))])):t.type==="code"?(s(),o("pre",X,[c("code",null,m(t.content),1)])):N("",!0)],64))),128))])]),_:1})]),_:1}))}}),dn=P(nn,[["__scopeId","data-v-e235a788"]]);export{dn as default};
