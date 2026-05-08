import{bE as E,bq as F,a as p,b as H,d as N,h as v,z as O,a$ as V,F as $,q as j,s as w,k,aF as q,aZ as y}from"./index-TyYsfcXJ.js";let S=!1;function A(){if(E&&window.CSS&&!S&&(S=!0,"registerProperty"in(window==null?void 0:window.CSS)))try{CSS.registerProperty({name:"--n-color-start",syntax:"<color>",inherits:!1,initialValue:"#0000"}),CSS.registerProperty({name:"--n-color-end",syntax:"<color>",inherits:!1,initialValue:"#0000"})}catch{}}function L(e){const{heightSmall:i,heightMedium:r,heightLarge:a,borderRadius:s}=e;return{color:"#eee",colorEnd:"#ddd",borderRadius:s,heightSmall:i,heightMedium:r,heightLarge:a}}const T={common:F,self:L},I=p([H("skeleton",`
 height: 1em;
 width: 100%;
 transition:
 --n-color-start .3s var(--n-bezier),
 --n-color-end .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 animation: 2s skeleton-loading infinite cubic-bezier(0.36, 0, 0.64, 1);
 background-color: var(--n-color-start);
 `),p("@keyframes skeleton-loading",`
 0% {
 background: var(--n-color-start);
 }
 40% {
 background: var(--n-color-end);
 }
 80% {
 background: var(--n-color-start);
 }
 100% {
 background: var(--n-color-start);
 }
 `)]),K=Object.assign(Object.assign({},w.props),{text:Boolean,round:Boolean,circle:Boolean,height:[String,Number],width:[String,Number],size:String,repeat:{type:Number,default:1},animated:{type:Boolean,default:!0},sharp:{type:Boolean,default:!0}}),W=N({name:"Skeleton",inheritAttrs:!1,props:K,setup(e){A();const{mergedClsPrefixRef:i,mergedComponentPropsRef:r}=j(e),a=k(()=>{var n,o;return e.size||((o=(n=r==null?void 0:r.value)===null||n===void 0?void 0:n.Skeleton)===null||o===void 0?void 0:o.size)}),s=w("Skeleton","-skeleton",I,T,e,i);return{mergedClsPrefix:i,style:k(()=>{var n,o;const m=s.value,{common:{cubicBezierEaseInOut:z}}=m,h=m.self,{color:_,colorEnd:x,borderRadius:C}=h;let l;const{circle:d,sharp:P,round:B,width:t,height:c,text:f,animated:R}=e,b=a.value;b!==void 0&&(l=h[q("height",b)]);const u=d?(n=t??c)!==null&&n!==void 0?n:l:t,g=(o=d?t??c:c)!==null&&o!==void 0?o:l;return{display:f?"inline-block":"",verticalAlign:f?"-0.125em":"",borderRadius:d?"50%":B?"4096px":P?"":C,width:typeof u=="number"?y(u):u,height:typeof g=="number"?y(g):g,animation:R?"":"none","--n-bezier":z,"--n-color-start":_,"--n-color-end":x}})}},render(){const{repeat:e,style:i,mergedClsPrefix:r,$attrs:a}=this,s=v("div",O({class:`${r}-skeleton`,style:i},a));return e>1?v($,null,V(e,null).map(n=>[s,`
`])):s}});export{W as _};
