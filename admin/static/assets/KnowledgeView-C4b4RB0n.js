import{d as He,h as C,o as v,au as Ft,av as Nt,aw as et,ax as Ht,c as Dt,ay as Mt,j as Bt,z as Ut,F as ie,N as qt,r as Vt,az as Kt,k as Z,as as Gt,b as p,e as _,a as H,f as Q,g as Xt,aA as ct,ap as xt,V as ut,q as Qt,s as jt,v as Yt,S as pt,O as At,w as Jt,x as Zt,B as wt,aB as ea,a1 as ta,a2 as aa,aC as na,aD as oa,aE as ra,P as ft,aF as pe,aG as Je,p as sa,A as fe,C as Ze,ah as la,a8 as J,J as D,M as d,D as b,L as r,E as T,ab as z,aa as u,ag as Ce,Z as c,G as a,a4 as bt,_ as Y,af as ia,ae as be,ai as da,aj as kt,al as Ne,a9 as ca,K as ua,a6 as pa}from"./index-TyYsfcXJ.js";import{A as fa,_ as ba}from"./AppPage-BXx7O9mz.js";import{E as ae}from"./EmptyState-DbdnN5GW.js";import{P as We}from"./PageToolbar-B544egpd.js";import{L as vt}from"./LayersOutline-BPRcwl5O.js";import{D as Ct}from"./DocumentTextOutline-B3xT3TbR.js";import{F as ge}from"./FlashOutline-C4L612vE.js";import{R as va}from"./RefreshOutline-CggdrbLk.js";import{A as ga}from"./Add-CbyKUl0Y.js";import{c as ha,a as St,o as ma}from"./cssr-COjPxOwu.js";import{_ as ya}from"./Skeleton-OWZSZSbu.js";import{_ as _a}from"./Alert-doIhGTI_.js";import{_ as xa}from"./Space-CCeiGOFb.js";const wa=St(".v-x-scroll",{overflow:"auto",scrollbarWidth:"none"},[St("&::-webkit-scrollbar",{width:0,height:0})]),ka=He({name:"XScroll",props:{disabled:Boolean,onScroll:Function},setup(){const t=v(null);function i(x){!(x.currentTarget.offsetWidth<x.currentTarget.scrollWidth)||x.deltaY===0||(x.currentTarget.scrollLeft+=x.deltaY+x.deltaX,x.preventDefault())}const y=Ft();return wa.mount({id:"vueuc/x-scroll",head:!0,anchorMetaName:ha,ssr:y}),Object.assign({selfRef:t,handleWheel:i},{scrollTo(...x){var A;(A=t.value)===null||A===void 0||A.scrollTo(...x)}})},render(){return C("div",{ref:"selfRef",onScroll:this.onScroll,onWheel:this.disabled?void 0:this.handleWheel,class:"v-x-scroll"},this.$slots)}});var Ca=/\s/;function Sa(t){for(var i=t.length;i--&&Ca.test(t.charAt(i)););return i}var $a=/^\s+/;function Ta(t){return t&&t.slice(0,Sa(t)+1).replace($a,"")}var $t=NaN,Ra=/^[-+]0x[0-9a-f]+$/i,za=/^0b[01]+$/i,Pa=/^0o[0-7]+$/i,Ba=parseInt;function Tt(t){if(typeof t=="number")return t;if(Nt(t))return $t;if(et(t)){var i=typeof t.valueOf=="function"?t.valueOf():t;t=et(i)?i+"":i}if(typeof t!="string")return t===0?t:+t;t=Ta(t);var y=za.test(t);return y||Pa.test(t)?Ba(t.slice(2),y?2:8):Ra.test(t)?$t:+t}var gt=function(){return Ht.Date.now()},ja="Expected a function",Aa=Math.max,Wa=Math.min;function Ea(t,i,y){var R,x,A,w,k,P,B=0,S=!1,W=!1,M=!0;if(typeof t!="function")throw new TypeError(ja);i=Tt(i)||0,et(y)&&(S=!!y.leading,W="maxWait"in y,A=W?Aa(Tt(y.maxWait)||0,i):A,M="trailing"in y?!!y.trailing:M);function E(g){var q=R,N=x;return R=x=void 0,B=g,w=t.apply(N,q),w}function L(g){return B=g,k=setTimeout(U,i),S?E(g):w}function I(g){var q=g-P,N=g-B,ne=i-q;return W?Wa(ne,A-N):ne}function V(g){var q=g-P,N=g-B;return P===void 0||q>=i||q<0||W&&N>=A}function U(){var g=gt();if(V(g))return F(g);k=setTimeout(U,I(g))}function F(g){return k=void 0,M&&R?E(g):(R=x=void 0,w)}function ee(){k!==void 0&&clearTimeout(k),B=0,R=P=x=k=void 0}function G(){return k===void 0?w:F(gt())}function $(){var g=gt(),q=V(g);if(R=arguments,x=this,P=g,q){if(k===void 0)return L(P);if(W)return clearTimeout(k),k=setTimeout(U,i),E(P)}return k===void 0&&(k=setTimeout(U,i)),w}return $.cancel=ee,$.flush=G,$}var La="Expected a function";function Ia(t,i,y){var R=!0,x=!0;if(typeof t!="function")throw new TypeError(La);return et(y)&&(R="leading"in y?!!y.leading:R,x="trailing"in y?!!y.trailing:x),Ea(t,i,{leading:R,maxWait:i,trailing:x})}const _t=Dt("n-tabs"),Wt={tab:[String,Number,Object,Function],name:{type:[String,Number],required:!0},disabled:Boolean,displayDirective:{type:String,default:"if"},closable:{type:Boolean,default:void 0},tabProps:Object,label:[String,Number,Object,Function]},Oa=He({__TAB_PANE__:!0,name:"TabPane",alias:["TabPanel"],props:Wt,slots:Object,setup(t){const i=Bt(_t,null);return i||Mt("tab-pane","`n-tab-pane` must be placed inside `n-tabs`."),{style:i.paneStyleRef,class:i.paneClassRef,mergedClsPrefix:i.mergedClsPrefixRef}},render(){return C("div",{class:[`${this.mergedClsPrefix}-tab-pane`,this.class],style:this.style},this.$slots)}}),Fa=Object.assign({internalLeftPadded:Boolean,internalAddable:Boolean,internalCreatedByPane:Boolean},Gt(Wt,["displayDirective"])),yt=He({__TAB__:!0,inheritAttrs:!1,name:"Tab",props:Fa,setup(t){const{mergedClsPrefixRef:i,valueRef:y,typeRef:R,closableRef:x,tabStyleRef:A,addTabStyleRef:w,tabClassRef:k,addTabClassRef:P,tabChangeIdRef:B,onBeforeLeaveRef:S,triggerRef:W,handleAdd:M,activateTab:E,handleClose:L}=Bt(_t);return{trigger:W,mergedClosable:Z(()=>{if(t.internalAddable)return!1;const{closable:I}=t;return I===void 0?x.value:I}),style:A,addStyle:w,tabClass:k,addTabClass:P,clsPrefix:i,value:y,type:R,handleClose(I){I.stopPropagation(),!t.disabled&&L(t.name)},activateTab(){if(t.disabled)return;if(t.internalAddable){M();return}const{name:I}=t,V=++B.id;if(I!==y.value){const{value:U}=S;U?Promise.resolve(U(t.name,y.value)).then(F=>{F&&B.id===V&&E(I)}):E(I)}}}},render(){const{internalAddable:t,clsPrefix:i,name:y,disabled:R,label:x,tab:A,value:w,mergedClosable:k,trigger:P,$slots:{default:B}}=this,S=x??A;return C("div",{class:`${i}-tabs-tab-wrapper`},this.internalLeftPadded?C("div",{class:`${i}-tabs-tab-pad`}):null,C("div",Object.assign({key:y,"data-name":y,"data-disabled":R?!0:void 0},Ut({class:[`${i}-tabs-tab`,w===y&&`${i}-tabs-tab--active`,R&&`${i}-tabs-tab--disabled`,k&&`${i}-tabs-tab--closable`,t&&`${i}-tabs-tab--addable`,t?this.addTabClass:this.tabClass],onClick:P==="click"?this.activateTab:void 0,onMouseenter:P==="hover"?this.activateTab:void 0,style:t?this.addStyle:this.style},this.internalCreatedByPane?this.tabProps||{}:this.$attrs)),C("span",{class:`${i}-tabs-tab__label`},t?C(ie,null,C("div",{class:`${i}-tabs-tab__height-placeholder`}," "),C(qt,{clsPrefix:i},{default:()=>C(ga,null)})):B?B():typeof S=="object"?S:Vt(S??y)),k&&this.type==="card"?C(Kt,{clsPrefix:i,class:`${i}-tabs-tab__close`,onClick:this.handleClose,disabled:R}):null))}}),Na=p("tabs",`
 box-sizing: border-box;
 width: 100%;
 display: flex;
 flex-direction: column;
 transition:
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
`,[_("segment-type",[p("tabs-rail",[H("&.transition-disabled",[p("tabs-capsule",`
 transition: none;
 `)])])]),_("top",[p("tab-pane",`
 padding: var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left);
 `)]),_("left",[p("tab-pane",`
 padding: var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left) var(--n-pane-padding-top);
 `)]),_("left, right",`
 flex-direction: row;
 `,[p("tabs-bar",`
 width: 2px;
 right: 0;
 transition:
 top .2s var(--n-bezier),
 max-height .2s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 padding: var(--n-tab-padding-vertical); 
 `)]),_("right",`
 flex-direction: row-reverse;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-left) var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom);
 `),p("tabs-bar",`
 left: 0;
 `)]),_("bottom",`
 flex-direction: column-reverse;
 justify-content: flex-end;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-bottom) var(--n-pane-padding-right) var(--n-pane-padding-top) var(--n-pane-padding-left);
 `),p("tabs-bar",`
 top: 0;
 `)]),p("tabs-rail",`
 position: relative;
 padding: 3px;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 background-color: var(--n-color-segment);
 transition: background-color .3s var(--n-bezier);
 display: flex;
 align-items: center;
 `,[p("tabs-capsule",`
 border-radius: var(--n-tab-border-radius);
 position: absolute;
 pointer-events: none;
 background-color: var(--n-tab-color-segment);
 box-shadow: 0 1px 3px 0 rgba(0, 0, 0, .08);
 transition: transform 0.3s var(--n-bezier);
 `),p("tabs-tab-wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[p("tabs-tab",`
 overflow: hidden;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[_("active",`
 font-weight: var(--n-font-weight-strong);
 color: var(--n-tab-text-color-active);
 `),H("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])])]),_("flex",[p("tabs-nav",`
 width: 100%;
 position: relative;
 `,[p("tabs-wrapper",`
 width: 100%;
 `,[p("tabs-tab",`
 margin-right: 0;
 `)])])]),p("tabs-nav",`
 box-sizing: border-box;
 line-height: 1.5;
 display: flex;
 transition: border-color .3s var(--n-bezier);
 `,[Q("prefix, suffix",`
 display: flex;
 align-items: center;
 `),Q("prefix","padding-right: 16px;"),Q("suffix","padding-left: 16px;")]),_("top, bottom",[H(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[H("&::before",`
 top: 0;
 bottom: 0;
 left: 0;
 width: 20px;
 `),H("&::after",`
 top: 0;
 bottom: 0;
 right: 0;
 width: 20px;
 `),_("shadow-start",[H("&::before",`
 box-shadow: inset 10px 0 8px -8px rgba(0, 0, 0, .12);
 `)]),_("shadow-end",[H("&::after",`
 box-shadow: inset -10px 0 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),_("left, right",[p("tabs-nav-scroll-content",`
 flex-direction: column;
 `),H(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[H("&::before",`
 top: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),H("&::after",`
 bottom: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),_("shadow-start",[H("&::before",`
 box-shadow: inset 0 10px 8px -8px rgba(0, 0, 0, .12);
 `)]),_("shadow-end",[H("&::after",`
 box-shadow: inset 0 -10px 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),p("tabs-nav-scroll-wrapper",`
 flex: 1;
 position: relative;
 overflow: hidden;
 `,[p("tabs-nav-y-scroll",`
 height: 100%;
 width: 100%;
 overflow-y: auto; 
 scrollbar-width: none;
 `,[H("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",`
 width: 0;
 height: 0;
 display: none;
 `)]),H("&::before, &::after",`
 transition: box-shadow .3s var(--n-bezier);
 pointer-events: none;
 content: "";
 position: absolute;
 z-index: 1;
 `)]),p("tabs-nav-scroll-content",`
 display: flex;
 position: relative;
 min-width: 100%;
 min-height: 100%;
 width: fit-content;
 box-sizing: border-box;
 `),p("tabs-wrapper",`
 display: inline-flex;
 flex-wrap: nowrap;
 position: relative;
 `),p("tabs-tab-wrapper",`
 display: flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 flex-grow: 0;
 `),p("tabs-tab",`
 cursor: pointer;
 white-space: nowrap;
 flex-wrap: nowrap;
 display: inline-flex;
 align-items: center;
 color: var(--n-tab-text-color);
 font-size: var(--n-tab-font-size);
 background-clip: padding-box;
 padding: var(--n-tab-padding);
 transition:
 box-shadow .3s var(--n-bezier),
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[_("disabled",{cursor:"not-allowed"}),Q("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),Q("label",`
 display: flex;
 align-items: center;
 z-index: 1;
 `)]),p("tabs-bar",`
 position: absolute;
 bottom: 0;
 height: 2px;
 border-radius: 1px;
 background-color: var(--n-bar-color);
 transition:
 left .2s var(--n-bezier),
 max-width .2s var(--n-bezier),
 opacity .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `,[H("&.transition-disabled",`
 transition: none;
 `),_("disabled",`
 background-color: var(--n-tab-text-color-disabled)
 `)]),p("tabs-pane-wrapper",`
 position: relative;
 overflow: hidden;
 transition: max-height .2s var(--n-bezier);
 `),p("tab-pane",`
 color: var(--n-pane-text-color);
 width: 100%;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .2s var(--n-bezier);
 left: 0;
 right: 0;
 top: 0;
 `,[H("&.next-transition-leave-active, &.prev-transition-leave-active, &.next-transition-enter-active, &.prev-transition-enter-active",`
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 transform .2s var(--n-bezier),
 opacity .2s var(--n-bezier);
 `),H("&.next-transition-leave-active, &.prev-transition-leave-active",`
 position: absolute;
 `),H("&.next-transition-enter-from, &.prev-transition-leave-to",`
 transform: translateX(32px);
 opacity: 0;
 `),H("&.next-transition-leave-to, &.prev-transition-enter-from",`
 transform: translateX(-32px);
 opacity: 0;
 `),H("&.next-transition-leave-from, &.next-transition-enter-to, &.prev-transition-leave-from, &.prev-transition-enter-to",`
 transform: translateX(0);
 opacity: 1;
 `)]),p("tabs-tab-pad",`
 box-sizing: border-box;
 width: var(--n-tab-gap);
 flex-grow: 0;
 flex-shrink: 0;
 `),_("line-type, bar-type",[p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 box-sizing: border-box;
 vertical-align: bottom;
 `,[H("&:hover",{color:"var(--n-tab-text-color-hover)"}),_("active",`
 color: var(--n-tab-text-color-active);
 font-weight: var(--n-tab-font-weight-active);
 `),_("disabled",{color:"var(--n-tab-text-color-disabled)"})])]),p("tabs-nav",[_("line-type",[_("top",[Q("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 bottom: -1px;
 `)]),_("left",[Q("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 right: -1px;
 `)]),_("right",[Q("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 left: -1px;
 `)]),_("bottom",[Q("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 top: -1px;
 `)]),Q("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-nav-scroll-content",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-bar",`
 border-radius: 0;
 `)]),_("card-type",[Q("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-pad",`
 flex-grow: 1;
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab-pad",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 border: 1px solid var(--n-tab-border-color);
 background-color: var(--n-tab-color);
 box-sizing: border-box;
 position: relative;
 vertical-align: bottom;
 display: flex;
 justify-content: space-between;
 font-size: var(--n-tab-font-size);
 color: var(--n-tab-text-color);
 `,[_("addable",`
 padding-left: 8px;
 padding-right: 8px;
 font-size: 16px;
 justify-content: center;
 `,[Q("height-placeholder",`
 width: 0;
 font-size: var(--n-tab-font-size);
 `),Xt("disabled",[H("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])]),_("closable","padding-right: 8px;"),_("active",`
 background-color: #0000;
 font-weight: var(--n-tab-font-weight-active);
 color: var(--n-tab-text-color-active);
 `),_("disabled","color: var(--n-tab-text-color-disabled);")])]),_("left, right",`
 flex-direction: column; 
 `,[Q("prefix, suffix",`
 padding: var(--n-tab-padding-vertical);
 `),p("tabs-wrapper",`
 flex-direction: column;
 `),p("tabs-tab-wrapper",`
 flex-direction: column;
 `,[p("tabs-tab-pad",`
 height: var(--n-tab-gap-vertical);
 width: 100%;
 `)])]),_("top",[_("card-type",[p("tabs-scroll-padding","border-bottom: 1px solid var(--n-tab-border-color);"),Q("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-top-right-radius: var(--n-tab-border-radius);
 `,[_("active",`
 border-bottom: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `)])]),_("left",[_("card-type",[p("tabs-scroll-padding","border-right: 1px solid var(--n-tab-border-color);"),Q("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-bottom-left-radius: var(--n-tab-border-radius);
 `,[_("active",`
 border-right: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `)])]),_("right",[_("card-type",[p("tabs-scroll-padding","border-left: 1px solid var(--n-tab-border-color);"),Q("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-right-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[_("active",`
 border-left: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `)])]),_("bottom",[_("card-type",[p("tabs-scroll-padding","border-top: 1px solid var(--n-tab-border-color);"),Q("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-bottom-left-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[_("active",`
 border-top: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `)])])])]),ht=Ia,Ha=Object.assign(Object.assign({},jt.props),{value:[String,Number],defaultValue:[String,Number],trigger:{type:String,default:"click"},type:{type:String,default:"bar"},closable:Boolean,justifyContent:String,size:String,placement:{type:String,default:"top"},tabStyle:[String,Object],tabClass:String,addTabStyle:[String,Object],addTabClass:String,barWidth:Number,paneClass:String,paneStyle:[String,Object],paneWrapperClass:String,paneWrapperStyle:[String,Object],addable:[Boolean,Object],tabsPadding:{type:Number,default:0},animated:Boolean,onBeforeLeave:Function,onAdd:Function,"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array],onClose:[Function,Array],labelSize:String,activeName:[String,Number],onActiveNameChange:[Function,Array]}),Da=He({name:"Tabs",props:Ha,slots:Object,setup(t,{slots:i}){var y,R,x,A;const{mergedClsPrefixRef:w,inlineThemeDisabled:k,mergedComponentPropsRef:P}=Qt(t),B=jt("Tabs","-tabs",Na,ra,t,w),S=v(null),W=v(null),M=v(null),E=v(null),L=v(null),I=v(null),V=v(!0),U=v(!0),F=wt(t,["labelSize","size"]),ee=Z(()=>{var s,l;if(F.value)return F.value;const f=(l=(s=P==null?void 0:P.value)===null||s===void 0?void 0:s.Tabs)===null||l===void 0?void 0:l.size;return f||"medium"}),G=wt(t,["activeName","value"]),$=v((R=(y=G.value)!==null&&y!==void 0?y:t.defaultValue)!==null&&R!==void 0?R:i.default?(A=(x=ct(i.default())[0])===null||x===void 0?void 0:x.props)===null||A===void 0?void 0:A.name:null),g=Yt(G,$),q={id:0},N=Z(()=>{if(!(!t.justifyContent||t.type==="card"))return{display:"flex",justifyContent:t.justifyContent}});pt(g,()=>{q.id=0,se(),Pe()});function ne(){var s;const{value:l}=g;return l===null?null:(s=S.value)===null||s===void 0?void 0:s.querySelector(`[data-name="${l}"]`)}function he(s){if(t.type==="card")return;const{value:l}=W;if(!l)return;const f=l.style.opacity==="0";if(s){const m=`${w.value}-tabs-bar--disabled`,{barWidth:O,placement:re}=t;if(s.dataset.disabled==="true"?l.classList.add(m):l.classList.remove(m),["top","bottom"].includes(re)){if(Se(["top","maxHeight","height"]),typeof O=="number"&&s.offsetWidth>=O){const te=Math.floor((s.offsetWidth-O)/2)+s.offsetLeft;l.style.left=`${te}px`,l.style.maxWidth=`${O}px`}else l.style.left=`${s.offsetLeft}px`,l.style.maxWidth=`${s.offsetWidth}px`;l.style.width="8192px",f&&(l.style.transition="none"),l.offsetWidth,f&&(l.style.transition="",l.style.opacity="1")}else{if(Se(["left","maxWidth","width"]),typeof O=="number"&&s.offsetHeight>=O){const te=Math.floor((s.offsetHeight-O)/2)+s.offsetTop;l.style.top=`${te}px`,l.style.maxHeight=`${O}px`}else l.style.top=`${s.offsetTop}px`,l.style.maxHeight=`${s.offsetHeight}px`;l.style.height="8192px",f&&(l.style.transition="none"),l.offsetHeight,f&&(l.style.transition="",l.style.opacity="1")}}}function me(){if(t.type==="card")return;const{value:s}=W;s&&(s.style.opacity="0")}function Se(s){const{value:l}=W;if(l)for(const f of s)l.style[f]=""}function se(){if(t.type==="card")return;const s=ne();s?he(s):me()}function Pe(){var s;const l=(s=L.value)===null||s===void 0?void 0:s.$el;if(!l)return;const f=ne();if(!f)return;const{scrollLeft:m,offsetWidth:O}=l,{offsetLeft:re,offsetWidth:te}=f;m>re?l.scrollTo({top:0,left:re,behavior:"smooth"}):re+te>m+O&&l.scrollTo({top:0,left:re+te-O,behavior:"smooth"})}const X=v(null);let ve=0,oe=null;function ye(s){const l=X.value;if(l){ve=s.getBoundingClientRect().height;const f=`${ve}px`,m=()=>{l.style.height=f,l.style.maxHeight=f};oe?(m(),oe(),oe=null):oe=m}}function Ee(s){const l=X.value;if(l){const f=s.getBoundingClientRect().height,m=()=>{document.body.offsetHeight,l.style.maxHeight=`${f}px`,l.style.height=`${Math.max(ve,f)}px`};oe?(oe(),oe=null,m()):oe=m}}function De(){const s=X.value;if(s){s.style.maxHeight="",s.style.height="";const{paneWrapperStyle:l}=t;if(typeof l=="string")s.style.cssText=l;else if(l){const{maxHeight:f,height:m}=l;f!==void 0&&(s.style.maxHeight=f),m!==void 0&&(s.style.height=m)}}}const Le={value:[]},Be=v("next");function tt(s){const l=g.value;let f="next";for(const m of Le.value){if(m===l)break;if(m===s){f="prev";break}}Be.value=f,Me(s)}function Me(s){const{onActiveNameChange:l,onUpdateValue:f,"onUpdate:value":m}=t;l&&Ze(l,s),f&&Ze(f,s),m&&Ze(m,s),$.value=s}function at(s){const{onClose:l}=t;l&&Ze(l,s)}function Ie(){const{value:s}=W;if(!s)return;const l="transition-disabled";s.classList.add(l),se(),s.classList.remove(l)}const _e=v(null);function Oe({transitionDisabled:s}){const l=S.value;if(!l)return;s&&l.classList.add("transition-disabled");const f=ne();f&&_e.value&&(_e.value.style.width=`${f.offsetWidth}px`,_e.value.style.height=`${f.offsetHeight}px`,_e.value.style.transform=`translateX(${f.offsetLeft-ea(getComputedStyle(l).paddingLeft)}px)`,s&&_e.value.offsetWidth),s&&l.classList.remove("transition-disabled")}pt([g],()=>{t.type==="segment"&&ft(()=>{Oe({transitionDisabled:!1})})}),At(()=>{t.type==="segment"&&Oe({transitionDisabled:!0})});let Ue=0;function qe(s){var l;if(s.contentRect.width===0&&s.contentRect.height===0||Ue===s.contentRect.width)return;Ue=s.contentRect.width;const{type:f}=t;if((f==="line"||f==="bar")&&Ie(),f!=="segment"){const{placement:m}=t;je((m==="top"||m==="bottom"?(l=L.value)===null||l===void 0?void 0:l.$el:I.value)||null)}}const Ve=ht(qe,64);pt([()=>t.justifyContent,()=>t.size],()=>{ft(()=>{const{type:s}=t;(s==="line"||s==="bar")&&Ie()})});const xe=v(!1);function nt(s){var l;const{target:f,contentRect:{width:m,height:O}}=s,re=f.parentElement.parentElement.offsetWidth,te=f.parentElement.parentElement.offsetHeight,{placement:we}=t;if(!xe.value)we==="top"||we==="bottom"?re<m&&(xe.value=!0):te<O&&(xe.value=!0);else{const{value:Te}=E;if(!Te)return;we==="top"||we==="bottom"?re-m>Te.$el.offsetWidth&&(xe.value=!1):te-O>Te.$el.offsetHeight&&(xe.value=!1)}je(((l=L.value)===null||l===void 0?void 0:l.$el)||null)}const Ke=ht(nt,64);function Ge(){const{onAdd:s}=t;s&&s(),ft(()=>{const l=ne(),{value:f}=L;!l||!f||f.scrollTo({left:l.offsetLeft,top:0,behavior:"smooth"})})}function je(s){if(!s)return;const{placement:l}=t;if(l==="top"||l==="bottom"){const{scrollLeft:f,scrollWidth:m,offsetWidth:O}=s;V.value=f<=0,U.value=f+O>=m}else{const{scrollTop:f,scrollHeight:m,offsetHeight:O}=s;V.value=f<=0,U.value=f+O>=m}}const Xe=ht(s=>{je(s.target)},64);sa(_t,{triggerRef:fe(t,"trigger"),tabStyleRef:fe(t,"tabStyle"),tabClassRef:fe(t,"tabClass"),addTabStyleRef:fe(t,"addTabStyle"),addTabClassRef:fe(t,"addTabClass"),paneClassRef:fe(t,"paneClass"),paneStyleRef:fe(t,"paneStyle"),mergedClsPrefixRef:w,typeRef:fe(t,"type"),closableRef:fe(t,"closable"),valueRef:g,tabChangeIdRef:q,onBeforeLeaveRef:fe(t,"onBeforeLeave"),activateTab:tt,handleClose:at,handleAdd:Ge}),ma(()=>{se(),Pe()}),Jt(()=>{const{value:s}=M;if(!s)return;const{value:l}=w,f=`${l}-tabs-nav-scroll-wrapper--shadow-start`,m=`${l}-tabs-nav-scroll-wrapper--shadow-end`;V.value?s.classList.remove(f):s.classList.add(f),U.value?s.classList.remove(m):s.classList.add(m)});const $e={syncBarPosition:()=>{se()}},Qe=()=>{Oe({transitionDisabled:!0})},Ye=Z(()=>{const{value:s}=ee,{type:l}=t,f={card:"Card",bar:"Bar",line:"Line",segment:"Segment"}[l],m=`${s}${f}`,{self:{barColor:O,closeIconColor:re,closeIconColorHover:te,closeIconColorPressed:we,tabColor:Te,tabBorderColor:ot,paneTextColor:rt,tabFontWeight:st,tabBorderRadius:Ae,tabFontWeightActive:lt,colorSegment:ce,fontWeightStrong:o,tabColorSegment:e,closeSize:h,closeIconSize:ue,closeColorHover:K,closeColorPressed:Fe,closeBorderRadius:it,[pe("panePadding",s)]:Re,[pe("tabPadding",m)]:ke,[pe("tabPaddingVertical",m)]:le,[pe("tabGap",m)]:ze,[pe("tabGap",`${m}Vertical`)]:dt,[pe("tabTextColor",l)]:n,[pe("tabTextColorActive",l)]:j,[pe("tabTextColorHover",l)]:Et,[pe("tabTextColorDisabled",l)]:Lt,[pe("tabFontSize",s)]:It},common:{cubicBezierEaseInOut:Ot}}=B.value;return{"--n-bezier":Ot,"--n-color-segment":ce,"--n-bar-color":O,"--n-tab-font-size":It,"--n-tab-text-color":n,"--n-tab-text-color-active":j,"--n-tab-text-color-disabled":Lt,"--n-tab-text-color-hover":Et,"--n-pane-text-color":rt,"--n-tab-border-color":ot,"--n-tab-border-radius":Ae,"--n-close-size":h,"--n-close-icon-size":ue,"--n-close-color-hover":K,"--n-close-color-pressed":Fe,"--n-close-border-radius":it,"--n-close-icon-color":re,"--n-close-icon-color-hover":te,"--n-close-icon-color-pressed":we,"--n-tab-color":Te,"--n-tab-font-weight":st,"--n-tab-font-weight-active":lt,"--n-tab-padding":ke,"--n-tab-padding-vertical":le,"--n-tab-gap":ze,"--n-tab-gap-vertical":dt,"--n-pane-padding-left":Je(Re,"left"),"--n-pane-padding-right":Je(Re,"right"),"--n-pane-padding-top":Je(Re,"top"),"--n-pane-padding-bottom":Je(Re,"bottom"),"--n-font-weight-strong":o,"--n-tab-color-segment":e}}),de=k?Zt("tabs",Z(()=>`${ee.value[0]}${t.type[0]}`),Ye,t):void 0;return Object.assign({mergedClsPrefix:w,mergedValue:g,renderedNames:new Set,segmentCapsuleElRef:_e,tabsPaneWrapperRef:X,tabsElRef:S,barElRef:W,addTabInstRef:E,xScrollInstRef:L,scrollWrapperElRef:M,addTabFixed:xe,tabWrapperStyle:N,handleNavResize:Ve,mergedSize:ee,handleScroll:Xe,handleTabsResize:Ke,cssVars:k?void 0:Ye,themeClass:de==null?void 0:de.themeClass,animationDirection:Be,renderNameListRef:Le,yScrollElRef:I,handleSegmentResize:Qe,onAnimationBeforeLeave:ye,onAnimationEnter:Ee,onAnimationAfterEnter:De,onRender:de==null?void 0:de.onRender},$e)},render(){const{mergedClsPrefix:t,type:i,placement:y,addTabFixed:R,addable:x,mergedSize:A,renderNameListRef:w,onRender:k,paneWrapperClass:P,paneWrapperStyle:B,$slots:{default:S,prefix:W,suffix:M}}=this;k==null||k();const E=S?ct(S()).filter($=>$.type.__TAB_PANE__===!0):[],L=S?ct(S()).filter($=>$.type.__TAB__===!0):[],I=!L.length,V=i==="card",U=i==="segment",F=!V&&!U&&this.justifyContent;w.value=[];const ee=()=>{const $=C("div",{style:this.tabWrapperStyle,class:`${t}-tabs-wrapper`},F?null:C("div",{class:`${t}-tabs-scroll-padding`,style:y==="top"||y==="bottom"?{width:`${this.tabsPadding}px`}:{height:`${this.tabsPadding}px`}}),I?E.map((g,q)=>(w.value.push(g.props.name),mt(C(yt,Object.assign({},g.props,{internalCreatedByPane:!0,internalLeftPadded:q!==0&&(!F||F==="center"||F==="start"||F==="end")}),g.children?{default:g.children.tab}:void 0)))):L.map((g,q)=>(w.value.push(g.props.name),mt(q!==0&&!F?Pt(g):g))),!R&&x&&V?zt(x,(I?E.length:L.length)!==0):null,F?null:C("div",{class:`${t}-tabs-scroll-padding`,style:{width:`${this.tabsPadding}px`}}));return C("div",{ref:"tabsElRef",class:`${t}-tabs-nav-scroll-content`},V&&x?C(ut,{onResize:this.handleTabsResize},{default:()=>$}):$,V?C("div",{class:`${t}-tabs-pad`}):null,V?null:C("div",{ref:"barElRef",class:`${t}-tabs-bar`}))},G=U?"top":y;return C("div",{class:[`${t}-tabs`,this.themeClass,`${t}-tabs--${i}-type`,`${t}-tabs--${A}-size`,F&&`${t}-tabs--flex`,`${t}-tabs--${G}`],style:this.cssVars},C("div",{class:[`${t}-tabs-nav--${i}-type`,`${t}-tabs-nav--${G}`,`${t}-tabs-nav`]},xt(W,$=>$&&C("div",{class:`${t}-tabs-nav__prefix`},$)),U?C(ut,{onResize:this.handleSegmentResize},{default:()=>C("div",{class:`${t}-tabs-rail`,ref:"tabsElRef"},C("div",{class:`${t}-tabs-capsule`,ref:"segmentCapsuleElRef"},C("div",{class:`${t}-tabs-wrapper`},C("div",{class:`${t}-tabs-tab`}))),I?E.map(($,g)=>(w.value.push($.props.name),C(yt,Object.assign({},$.props,{internalCreatedByPane:!0,internalLeftPadded:g!==0}),$.children?{default:$.children.tab}:void 0))):L.map(($,g)=>(w.value.push($.props.name),g===0?$:Pt($))))}):C(ut,{onResize:this.handleNavResize},{default:()=>C("div",{class:`${t}-tabs-nav-scroll-wrapper`,ref:"scrollWrapperElRef"},["top","bottom"].includes(G)?C(ka,{ref:"xScrollInstRef",onScroll:this.handleScroll},{default:ee}):C("div",{class:`${t}-tabs-nav-y-scroll`,onScroll:this.handleScroll,ref:"yScrollElRef"},ee()))}),R&&x&&V?zt(x,!0):null,xt(M,$=>$&&C("div",{class:`${t}-tabs-nav__suffix`},$))),I&&(this.animated&&(G==="top"||G==="bottom")?C("div",{ref:"tabsPaneWrapperRef",style:B,class:[`${t}-tabs-pane-wrapper`,P]},Rt(E,this.mergedValue,this.renderedNames,this.onAnimationBeforeLeave,this.onAnimationEnter,this.onAnimationAfterEnter,this.animationDirection)):Rt(E,this.mergedValue,this.renderedNames)))}});function Rt(t,i,y,R,x,A,w){const k=[];return t.forEach(P=>{const{name:B,displayDirective:S,"display-directive":W}=P.props,M=L=>S===L||W===L,E=i===B;if(P.key!==void 0&&(P.key=B),E||M("show")||M("show:lazy")&&y.has(B)){y.has(B)||y.add(B);const L=!M("if");k.push(L?ta(P,[[aa,E]]):P)}}),w?C(na,{name:`${w}-transition`,onBeforeLeave:R,onEnter:x,onAfterEnter:A},{default:()=>k}):k}function zt(t,i){return C(yt,{ref:"addTabInstRef",key:"__addable",name:"__addable",internalCreatedByPane:!0,internalAddable:!0,internalLeftPadded:i,disabled:typeof t=="object"&&t.disabled})}function Pt(t){const i=oa(t);return i.props?i.props.internalLeftPadded=!0:i.props={internalLeftPadded:!0},i}function mt(t){return Array.isArray(t.dynamicProps)?t.dynamicProps.includes("internalLeftPadded")||t.dynamicProps.push("internalLeftPadded"):t.dynamicProps=["internalLeftPadded"],t}const Ma={class:"knowledge-hero__main"},Ua={class:"knowledge-hero__badges"},qa={class:"knowledge-status-grid"},Va={class:"knowledge-status"},Ka={class:"knowledge-status"},Ga={class:"knowledge-status"},Xa={key:0,class:"source-grid"},Qa={class:"source-card__head"},Ya={class:"source-card__meta"},Ja={key:0,class:"source-card__reason"},Za={key:0,class:"knowledge-empty-panel"},en={key:1,class:"knowledge-empty-panel"},tn={key:2,class:"result-list"},an={class:"result-card__head"},nn={key:0,class:"knowledge-empty-panel"},on={key:1,class:"knowledge-empty-panel"},rn={key:2,class:"context-layout"},sn={class:"section-head"},ln={key:0,class:"context-pack"},dn={class:"context-hit-list"},cn={class:"context-hit__head"},un={class:"context-hit__meta"},pn={key:0,class:"metrics-layout"},fn={class:"metrics-grid"},bn={class:"metrics-columns"},vn={key:0,class:"metric-ratio-list"},gn={key:0,class:"metric-ratio-list"},hn={class:"recent-context-list"},mn={class:"recent-context-card__main"},yn={class:"relationship-card__meta"},_n={class:"graph-layout"},xn={class:"section-head"},wn={key:0,class:"entity-list"},kn={class:"relationship-list"},Cn={class:"graph-scope-risk__head"},Sn={class:"graph-scope-risk__list"},$n={class:"relationship-card__triple"},Tn={class:"relationship-card__evidence"},Rn={class:"relationship-card__meta"},zn={key:0},Pn={key:0,class:"relationship-card__governance"},Bn={class:"relationship-card__rollback"},jn={class:"relationship-card__supersede"},An={key:0,class:"candidate-list"},Wn={class:"candidate-card__body"},En={class:"relationship-card__triple"},Ln={class:"relationship-card__meta"},In={class:"candidate-card__actions"},On=He({__name:"KnowledgeView",setup(t){const i=la(),y=v("sources"),R=v(!0),x=v(!1),A=v(!1),w=v(""),k=v(!1),P=v(!1),B=v(!1),S=v({}),W=v([]),M=v(""),E=v([]),L=v(!1),I=v(!1),V=v(""),U=v(""),F=v(""),ee=v(""),G=v(null),$=v(!1),g=v(!1),q=v(!1),N=v(null),ne=v([]),he=v([]),me=v([]),Se=v(!1),se=v(""),Pe=v({}),X=v({}),ve=v([]),oe=v(!1),ye=v(""),Ee=v({}),De=Z(()=>S.value.chunk_count||0),Le=Z(()=>S.value.source_count||W.value.length||0),Be=Z(()=>S.value.skipped_sources||W.value.filter(o=>o.status!=="indexed").length),tt=Z(()=>S.value.indexed_sources||W.value.filter(o=>o.status==="indexed").length),Me=Z(()=>ve.value.length),at=Z(()=>he.value.length),Ie=Z(()=>me.value.length),_e=Z(()=>B.value?Le.value?`已索引 ${tt.value} 个来源，跳过 ${Be.value} 个来源，共 ${De.value} 个文档片段。`:"还没有索引到文档源，可以检查插件配置或执行重建索引。":"知识库插件未启用或运行时实例暂不可用。"),Oe=Z(()=>{var o;return((o=G.value)==null?void 0:o.hits)||[]}),Ue=Z(()=>{var o;return((o=N.value)==null?void 0:o.recent)||[]});At(()=>{qe()});async function qe(){R.value=!0;try{await Promise.all([nt(),Ke(),$e(),de(),Ve()])}finally{R.value=!1}}async function Ve(){q.value=!0;try{const o=await J("/api/admin/context/metrics",{params:{limit:80}});N.value=o.metrics||null}catch(o){if(ce(o)){P.value=!0,w.value="当前运行后端还没有上下文指标 API；请重建/重启 Bot 后再查看评测指标。",N.value=null;return}i.error("上下文指标加载失败"),console.error("Failed to load context metrics:",o)}finally{q.value=!1}}async function xe(){x.value=!0;try{await qe(),i.success("知识系统状态已刷新")}catch(o){i.error("刷新知识系统失败"),console.error("Failed to refresh knowledge console:",o)}finally{x.value=!1}}async function nt(){try{const o=await J("/api/admin/knowledge/stats");B.value=!!o.available,S.value=o.stats||{}}catch(o){if(!ce(o))throw o;w.value="当前运行后端还没有新版知识库 API，已降级为旧版统计；完整图谱和上下文调试需要重建/重启 Bot。";const e=await J("/api/admin/knowledge");B.value=!!(e.available??!0),S.value={loaded:!0,chunk_count:e.entry_count||0,source_count:0,indexed_sources:0,skipped_sources:0,docs_dir:"docs"}}}async function Ke(){try{const o=await J("/api/admin/knowledge/sources");typeof o.available=="boolean"&&(B.value=o.available),W.value=o.sources||[]}catch(o){if(!ce(o))throw o;w.value="当前运行后端还没有新版知识库 API，文档源详情暂不可用；请重建/重启 Bot 后再查看。",W.value=[]}}async function Ge(){A.value=!0;try{const o=await J("/api/admin/knowledge/reindex",{method:"POST"});o.ok?(S.value=o.stats||S.value,i.success(`索引已重建：${o.entry_count||0} 个片段`),await Ke()):i.error(`重建索引失败：${o.error||"unknown"}`)}catch(o){if(ce(o)){w.value="当前运行后端还没有重建索引接口；请先重建/重启 Bot。",i.warning("当前后端不支持在线重建索引，请先重建/重启 Bot");return}i.error("重建索引失败"),console.error("Failed to reindex knowledge:",o)}finally{A.value=!1}}async function je(){const o=M.value.trim();if(!o){E.value=[],I.value=!1,V.value="";return}L.value=!0;try{let e;try{e=await J("/api/admin/knowledge/search",{params:{q:o,top_k:20}})}catch(h){if(!ce(h))throw h;w.value="当前运行后端还没有结构化搜索接口，已降级为旧版搜索结果。",e=await J("/api/admin/knowledge",{params:{q:o,top_k:20}})}E.value=e.results||[],I.value=!0,V.value=o}catch(e){i.error("知识库搜索失败"),console.error("Knowledge search failed:",e)}finally{L.value=!1}}async function Xe(){const o=U.value.trim();if(!o){G.value=null,g.value=!1;return}$.value=!0;try{const e={q:o,top_k:12,max_chars:3200};F.value.trim()&&(e.user_id=F.value.trim()),ee.value.trim()&&(e.group_id=ee.value.trim());const h=await J("/api/admin/context/search",{params:e});G.value=h.pack||{text:"",hits:[],omitted_count:0},g.value=!0}catch(e){if(ce(e)){P.value=!0,w.value="当前运行后端还没有 Context 调试接口；请重建/重启 Bot 后再使用上下文调试。",G.value=null,g.value=!0,i.warning("当前后端不支持上下文调试，请先重建/重启 Bot");return}i.error("上下文调试失败"),console.error("Context debug failed:",e)}finally{$.value=!1}}async function $e(){Se.value=!0;try{const[o,e,h]=await Promise.all([J("/api/admin/knowledge/graph/entities",{params:{limit:80}}),J("/api/admin/knowledge/graph/relationships",{params:{limit:120}}),J("/api/admin/knowledge/graph/scope-risks",{params:{limit:80}}).catch(ue=>{if(ce(ue))return{available:!1,relationships:[]};throw ue})]);ne.value=o.entities||[],he.value=e.relationships||[],me.value=h.relationships||[],lt(),k.value=!o.available&&!e.available}catch(o){if(ce(o)){k.value=!0,w.value="当前运行后端还没有图谱 API；请重建/重启 Bot 后再查看图谱。",ne.value=[],he.value=[],me.value=[];return}i.error("图谱信息加载失败"),console.error("Failed to load knowledge graph:",o)}finally{Se.value=!1}}async function Qe(o){se.value=o.fact_id;try{const e=await J(`/api/admin/knowledge/graph/relationships/${o.fact_id}/rollback`,{method:"POST",params:{note:Pe.value[o.fact_id]||""}});if(!e.ok){i.error(e.error||"事实回滚失败");return}i.success("事实已回滚"),await $e()}catch(e){i.error("事实回滚失败"),console.error("Failed to rollback graph relationship:",e)}finally{se.value=""}}async function Ye(o){const e=X.value[o.fact_id];if(!e||!e.subject.trim()||!e.predicate.trim()||!e.object.trim()){i.warning("请填写完整的新三元组");return}se.value=o.fact_id;try{const h=await J(`/api/admin/knowledge/graph/relationships/${o.fact_id}/supersede`,{method:"POST",body:{subject:e.subject.trim(),predicate:e.predicate.trim(),object:e.object.trim(),confidence:Math.max(.6,o.confidence||.85),source:"admin",note:e.note.trim()}});if(!h.ok){i.error(h.error||"事实取代失败");return}i.success("事实已取代"),await $e()}catch(h){i.error("事实取代失败"),console.error("Failed to supersede graph relationship:",h)}finally{se.value=""}}async function de(){oe.value=!0;try{const o=await J("/api/admin/knowledge/graph/candidates",{params:{status:"pending",limit:120}});ve.value=o.candidates||[]}catch(o){if(ce(o)){k.value=!0,w.value="当前运行后端还没有图谱候选 API；请重建/重启 Bot 后再查看候选队列。",ve.value=[];return}i.error("候选队列加载失败"),console.error("Failed to load graph candidates:",o)}finally{oe.value=!1}}async function s(o){ye.value=o.candidate_id;try{const e=await J(`/api/admin/knowledge/graph/candidates/${o.candidate_id}/approve`,{method:"POST"});if(!e.ok){i.error(e.error||"候选通过失败");return}i.success("候选已通过并写入图谱"),await Promise.all([de(),$e()])}catch(e){i.error("候选通过失败"),console.error("Failed to approve graph candidate:",e)}finally{ye.value=""}}async function l(o){ye.value=o.candidate_id;try{if(!(await J(`/api/admin/knowledge/graph/candidates/${o.candidate_id}/reject`,{method:"POST",params:{note:Ee.value[o.candidate_id]||""}})).ok){i.error("候选拒绝失败");return}i.success("候选已拒绝"),await de()}catch(e){i.error("候选拒绝失败"),console.error("Failed to reject graph candidate:",e)}finally{ye.value=""}}function f(o){return typeof o!="number"?"--":o.toFixed(o>=10?1:3)}function m(o){return typeof o!="number"?"--":`${Math.round(o*100)}%`}function O(o){return typeof o!="number"?"--":Math.round(o).toLocaleString()}function re(o){return o==="indexed"?"success":"warning"}function te(o){return o==="memory_card"?"记忆卡片":o==="doc_chunk"?"文档片段":"图谱事实"}function we(o){return o==="memory_card"?"success":o==="doc_chunk"?"info":"warning"}function Te(o){return o?o.slice(0,12):"--"}function ot(o){const e=o.evidence||{},h=e.id||e.card_id||e.chunk_id||"",ue=e.quote?` · ${String(e.quote).slice(0,80)}`:"";return h?`${String(h)}${ue}`:"未记录证据"}function rt(o){const e=o.evidence||[];return e.length&&e.map(h=>{const ue=h.id||h.card_id||h.chunk_id||"",K=h.quote?` · ${String(h.quote).slice(0,90)}`:"";return ue?`${String(ue)}${K}`:K.replace(/^ · /,"")}).filter(Boolean).join(" / ")||"未记录证据"}function st(o){const e=o.scope||"global",h=o.scope_id||"global";return e==="user"?`用户 ${h}`:e==="group"?`群 ${h}`:"全局"}function Ae(o){return Object.entries(o||{}).sort((e,h)=>h[1]-e[1]).slice(0,8)}function lt(){const o={};for(const e of he.value){const h=X.value[e.fact_id];o[e.fact_id]=h||{subject:e.subject,predicate:e.predicate,object:e.object,note:""}}X.value=o}function ce(o){var e;return((e=o==null?void 0:o.response)==null?void 0:e.status)===404}return(o,e)=>{const h=ba,ue=ua,K=ia,Fe=xa,it=ya,Re=_a,ke=Oa,le=da,ze=ca,dt=Da;return b(),D(fa,{title:"知识库",eyebrow:"Knowledge Console",description:"管理文档源、核对检索命中，并调试本轮对话最终会引用哪些记忆、文档和图谱事实。"},{action:d(()=>[c(Fe,{align:"center",size:12},{default:d(()=>[c(h,{round:"",size:"small",type:r(B)?"success":"warning"},{default:d(()=>[z(u(r(B)?"运行中":"未启用"),1)]),_:1},8,["type"]),c(K,{secondary:"",loading:r(x),onClick:xe},{icon:d(()=>[c(ue,{component:r(va)},null,8,["component"])]),default:d(()=>[e[6]||(e[6]=z(" 刷新 ",-1))]),_:1},8,["loading"]),c(K,{type:"primary",secondary:"",loading:r(A),onClick:Ge},{default:d(()=>[...e[7]||(e[7]=[z(" 重建索引 ",-1)])]),_:1},8,["loading"])]),_:1})]),default:d(()=>[r(R)?(b(),D(it,{key:0,repeat:5,text:""})):(b(),T(ie,{key:1},[r(w)?(b(),D(Re,{key:0,type:"warning",class:"knowledge-compat-alert","show-icon":!1},{default:d(()=>[z(u(r(w)),1)]),_:1})):Ce("",!0),c(Y,{bordered:"",elevated:"",class:"knowledge-hero"},{default:d(()=>[a("div",Ma,[a("div",null,[e[8]||(e[8]=a("p",{class:"knowledge-eyebrow"}," Context Knowledge System ",-1)),a("h3",null,u(r(_e)),1),e[9]||(e[9]=a("p",null," CardStore 仍是生产记忆权威来源；这里负责文档知识、上下文调试和派生图谱治理。 ",-1))]),a("div",Ua,[c(h,{round:"",size:"small"},{default:d(()=>[z(" 目录 "+u(r(S).docs_dir||"docs"),1)]),_:1}),c(h,{round:"",size:"small",type:r(S).recursive===!1?"warning":"info"},{default:d(()=>[z(u(r(S).recursive===!1?"仅一级目录":"递归扫描"),1)]),_:1},8,["type"]),c(h,{round:"",size:"small",type:r(S).index_persisted?"success":"default"},{default:d(()=>[z(u(r(S).index_persisted?"SQLite 索引":"内存索引"),1)]),_:1},8,["type"])])]),a("div",qa,[a("div",Va,[e[10]||(e[10]=a("span",null,"文档片段",-1)),a("strong",null,u(r(De)),1)]),a("div",Ka,[e[11]||(e[11]=a("span",null,"文档源",-1)),a("strong",null,u(r(Le)),1)]),a("div",{class:bt(["knowledge-status",{"knowledge-status--warn":r(Be)>0}])},[e[12]||(e[12]=a("span",null,"跳过源",-1)),a("strong",null,u(r(Be)),1)],2),a("div",Ga,[e[13]||(e[13]=a("span",null,"图谱事实",-1)),a("strong",null,u(r(at)),1)]),a("div",{class:bt(["knowledge-status",{"knowledge-status--warn":r(Me)>0}])},[e[14]||(e[14]=a("span",null,"候选待审",-1)),a("strong",null,u(r(Me)),1)],2),a("div",{class:bt(["knowledge-status",{"knowledge-status--warn":r(Ie)>0}])},[e[15]||(e[15]=a("span",null,"作用域待查",-1)),a("strong",null,u(r(Ie)),1)],2)])]),_:1}),c(dt,{value:r(y),"onUpdate:value":e[5]||(e[5]=n=>Ne(y)?y.value=n:null),type:"segment",animated:"",class:"knowledge-tabs"},{default:d(()=>[c(ke,{name:"sources",tab:"文档源"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[...e[16]||(e[16]=[a("span",{class:"knowledge-toolbar__title"},"索引来源",-1),a("span",{class:"knowledge-toolbar__hint"},"确认哪些 Markdown 文件已进入知识库，哪些被跳过。",-1)])]),right:d(()=>[c(K,{secondary:"",loading:r(A),onClick:Ge},{default:d(()=>[...e[17]||(e[17]=[z(" 重新扫描 ",-1)])]),_:1},8,["loading"])]),_:1}),r(W).length?(b(),T("div",Xa,[(b(!0),T(ie,null,be(r(W),n=>(b(),D(Y,{key:n.source,bordered:"",embedded:"",class:"source-card"},{default:d(()=>[a("div",Qa,[a("div",null,[a("strong",null,u(n.source),1),a("span",null,u(n.path),1)]),c(h,{round:"",size:"small",type:re(n.status)},{default:d(()=>[z(u(n.status==="indexed"?"已索引":"已跳过"),1)]),_:2},1032,["type"])]),a("div",Ya,[a("span",null,u(n.chunk_count)+" 个片段",1),a("span",null,"hash "+u(Te(n.source_hash)),1)]),n.skipped_reason?(b(),T("p",Ja," 跳过原因："+u(n.skipped_reason),1)):Ce("",!0)]),_:2},1024))),128))])):(b(),D(ae,{key:1,title:"还没有文档源",description:"知识库未启用、目录为空，或当前运行实例还没有完成索引。",icon:r(vt)},null,8,["icon"]))]),_:1}),c(ke,{name:"search",tab:"搜索核对"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[c(le,{value:r(M),"onUpdate:value":e[0]||(e[0]=n=>Ne(M)?M.value=n:null),clearable:"",placeholder:"输入关键词或问题，核对文档 chunk 命中",class:"knowledge-query-input",onKeyup:kt(je,["enter"])},null,8,["value"])]),right:d(()=>[r(I)?(b(),D(K,{key:0,secondary:"",onClick:e[1]||(e[1]=n=>{M.value="",E.value=[],I.value=!1})},{default:d(()=>[...e[18]||(e[18]=[z(" 清除 ",-1)])]),_:1})):Ce("",!0),c(K,{type:"primary",loading:r(L),onClick:je},{default:d(()=>[...e[19]||(e[19]=[z(" 搜索文档 ",-1)])]),_:1},8,["loading"])]),_:1}),c(ze,{show:r(L)},{default:d(()=>[r(I)?r(E).length===0?(b(),T("div",en,[c(ae,{title:"没有命中文档片段",description:`“${r(V)}” 没有命中知识库，可以换更短的词或检查文档源。`,icon:r(ge)},null,8,["description","icon"])])):(b(),T("div",tn,[(b(!0),T(ie,null,be(r(E),(n,j)=>(b(),D(Y,{key:n.chunk_id||n.id||`${n.source}-${j}`,bordered:"",embedded:"",class:"result-card"},{default:d(()=>[a("div",an,[a("div",null,[a("strong",null,u(n.title||n.source||`结果 ${j+1}`),1),a("span",null,u(n.chunk_id||n.id||n.source),1)]),c(h,{round:"",size:"small",type:"info"},{default:d(()=>[z(" score "+u(f(n.score)),1)]),_:2},1024)]),a("p",null,u(n.content),1)]),_:2},1024))),128))])):(b(),T("div",Za,[c(ae,{title:"输入一句话开始核对",description:"这里只检查文档知识库命中，不包含记忆卡片或图谱事实。",icon:r(Ct)},null,8,["icon"])]))]),_:1},8,["show"])]),_:1}),c(ke,{name:"context",tab:"上下文调试"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[c(le,{value:r(U),"onUpdate:value":e[2]||(e[2]=n=>Ne(U)?U.value=n:null),clearable:"",placeholder:"输入本轮用户消息，查看 memory/doc/graph 最终命中",class:"context-query-input",onKeyup:kt(Xe,["enter"])},null,8,["value"]),c(le,{value:r(F),"onUpdate:value":e[3]||(e[3]=n=>Ne(F)?F.value=n:null),clearable:"",placeholder:"用户 ID，可选",class:"context-id-input"},null,8,["value"]),c(le,{value:r(ee),"onUpdate:value":e[4]||(e[4]=n=>Ne(ee)?ee.value=n:null),clearable:"",placeholder:"群 ID，可选",class:"context-id-input"},null,8,["value"])]),right:d(()=>[c(K,{type:"primary",loading:r($),onClick:Xe},{default:d(()=>[...e[20]||(e[20]=[z(" 调试上下文 ",-1)])]),_:1},8,["loading"])]),_:1}),c(ze,{show:r($)},{default:d(()=>[r(g)?r(P)?(b(),T("div",on,[c(ae,{title:"当前后端还没有上下文调试接口",description:"请重建/重启 Bot，让后端 API 与新版前端保持一致。",icon:r(ge)},null,8,["icon"])])):(b(),T("div",rn,[c(Y,{bordered:"",elevated:"",class:"context-pack-card"},{default:d(()=>{var n;return[a("div",sn,[e[21]||(e[21]=a("div",null,[a("p",{class:"knowledge-eyebrow"},"Prompt Pack"),a("h3",null,"最终打包文本")],-1)),c(h,{round:"",size:"small"},{default:d(()=>{var j;return[z(" 省略 "+u(((j=r(G))==null?void 0:j.omitted_count)||0)+" 条 ",1)]}),_:1})]),(n=r(G))!=null&&n.text?(b(),T("pre",ln,u(r(G).text),1)):(b(),D(ae,{key:1,compact:"",title:"没有可注入上下文",description:"这次查询没有命中可打包内容。",icon:r(ge)},null,8,["icon"]))]}),_:1}),a("div",dn,[(b(!0),T(ie,null,be(r(Oe),n=>(b(),D(Y,{key:`${n.type}-${n.id}`,bordered:"",embedded:"",class:"context-hit"},{default:d(()=>[a("div",cn,[a("div",null,[a("strong",null,u(n.title||n.source||n.id),1),a("span",null,u(n.id),1)]),c(Fe,{size:6},{default:d(()=>[c(h,{round:"",size:"small",type:we(n.type)},{default:d(()=>[z(u(te(n.type)),1)]),_:2},1032,["type"]),c(h,{round:"",size:"small"},{default:d(()=>[z(u(f(n.score)),1)]),_:2},1024)]),_:2},1024)]),a("p",null,u(n.content),1),a("div",un,[a("span",null,u(n.scope||"global")+"/"+u(n.scope_id||"global"),1),a("span",null,u(n.retriever||"retriever"),1),a("span",null,u(n.source),1)])]),_:2},1024))),128))])])):(b(),T("div",nn,[c(ae,{title:"还没有调试上下文",description:"输入一句真实聊天内容，可以看到统一上下文会引用哪些记忆卡片、文档片段和图谱事实。",icon:r(vt)},null,8,["icon"])]))]),_:1},8,["show"])]),_:1}),c(ke,{name:"metrics",tab:"评测指标"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[...e[22]||(e[22]=[a("span",{class:"knowledge-toolbar__title"},"上下文质量指标",-1),a("span",{class:"knowledge-toolbar__hint"},"来自最近统一上下文检索，帮助观察 miss、重复和 Prompt pack 长度。",-1)])]),right:d(()=>[c(K,{secondary:"",loading:r(q),onClick:Ve},{default:d(()=>[...e[23]||(e[23]=[z(" 刷新指标 ",-1)])]),_:1},8,["loading"])]),_:1}),c(ze,{show:r(q)},{default:d(()=>[r(N)?(b(),T("div",pn,[a("div",fn,[c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[24]||(e[24]=a("span",null,"最近查询",-1)),a("strong",null,u(r(N).total_queries),1)]),_:1}),c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[25]||(e[25]=a("span",null,"Miss 率",-1)),a("strong",null,u(m(r(N).miss_rate)),1)]),_:1}),c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[26]||(e[26]=a("span",null,"平均 Pack",-1)),a("strong",null,u(O(r(N).avg_pack_chars)),1)]),_:1}),c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[27]||(e[27]=a("span",null,"最大 Pack",-1)),a("strong",null,u(O(r(N).max_pack_chars)),1)]),_:1}),c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[28]||(e[28]=a("span",null,"重复率",-1)),a("strong",null,u(m(r(N).duplicate_rate)),1)]),_:1}),c(Y,{bordered:"",embedded:"",class:"metric-mini-card"},{default:d(()=>[e[29]||(e[29]=a("span",null,"省略命中",-1)),a("strong",null,u(r(N).omitted_total),1)]),_:1})]),a("div",bn,[c(Y,{bordered:"",elevated:"",class:"metrics-panel"},{default:d(()=>[e[30]||(e[30]=a("div",{class:"section-head"},[a("div",null,[a("p",{class:"knowledge-eyebrow"},"Sources"),a("h3",null,"命中来源")])],-1)),Ae(r(N).hit_source_counts).length?(b(),T("div",vn,[(b(!0),T(ie,null,be(Ae(r(N).hit_source_counts),([n,j])=>(b(),T("div",{key:n,class:"metric-ratio-row"},[a("span",null,u(n||"unknown"),1),a("strong",null,u(j),1)]))),128))])):(b(),D(ae,{key:1,compact:"",title:"暂无来源命中",description:"还没有最近上下文检索记录。",icon:r(ge)},null,8,["icon"]))]),_:1}),c(Y,{bordered:"",elevated:"",class:"metrics-panel"},{default:d(()=>[e[31]||(e[31]=a("div",{class:"section-head"},[a("div",null,[a("p",{class:"knowledge-eyebrow"},"Types"),a("h3",null,"命中类型")])],-1)),Ae(r(N).hit_type_counts).length?(b(),T("div",gn,[(b(!0),T(ie,null,be(Ae(r(N).hit_type_counts),([n,j])=>(b(),T("div",{key:n,class:"metric-ratio-row"},[a("span",null,u(te(n)),1),a("strong",null,u(j),1)]))),128))])):(b(),D(ae,{key:1,compact:"",title:"暂无类型命中",description:"还没有最近上下文检索记录。",icon:r(ge)},null,8,["icon"]))]),_:1})]),a("div",hn,[(b(!0),T(ie,null,be(r(Ue),n=>(b(),D(Y,{key:`${n.created_at}-${n.query}`,bordered:"",embedded:"",class:"recent-context-card"},{default:d(()=>[a("div",mn,[a("strong",null,u(n.query||"空查询"),1),a("span",null,u(n.group_id?`群 ${n.group_id}`:n.user_id?`用户 ${n.user_id}`:"全局"),1)]),a("div",yn,[c(h,{round:"",size:"small",type:n.hit_count?"success":"warning"},{default:d(()=>[z(u(n.hit_count||0)+" 命中 ",1)]),_:2},1032,["type"]),a("span",null,"pack "+u(n.pack_chars||0),1),a("span",null,"重复 "+u(n.duplicate_count||0),1),a("span",null,"省略 "+u(n.omitted_count||0),1)])]),_:2},1024))),128))])])):(b(),D(ae,{key:1,title:"暂无上下文指标",description:"先在“上下文调试”输入一条消息，或等待 Bot 真实对话产生检索记录。",icon:r(ge)},null,8,["icon"]))]),_:1},8,["show"])]),_:1}),c(ke,{name:"graph",tab:"图谱关系"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[...e[32]||(e[32]=[a("span",{class:"knowledge-toolbar__title"},"已生效事实",-1),a("span",{class:"knowledge-toolbar__hint"},"图谱是派生事实层，可重建、可回滚，不替代记忆卡片。",-1)])]),right:d(()=>[c(K,{secondary:"",loading:r(Se),onClick:$e},{default:d(()=>[...e[33]||(e[33]=[z(" 刷新图谱 ",-1)])]),_:1},8,["loading"])]),_:1}),c(ze,{show:r(Se)},{default:d(()=>[a("div",_n,[c(Y,{bordered:"",elevated:"",class:"graph-entities"},{default:d(()=>[a("div",xn,[e[34]||(e[34]=a("div",null,[a("p",{class:"knowledge-eyebrow"},"Entities"),a("h3",null,"实体")],-1)),c(h,{round:"",size:"small"},{default:d(()=>[z(u(r(ne).length)+" 个 ",1)]),_:1})]),r(ne).length?(b(),T("div",wn,[(b(!0),T(ie,null,be(r(ne),n=>(b(),T("div",{key:n.name,class:"entity-row"},[a("span",null,u(n.name),1),c(h,{round:"",size:"small"},{default:d(()=>[z(u(n.fact_count)+" 条 ",1)]),_:2},1024)]))),128))])):(b(),D(ae,{key:1,compact:"",title:"暂无实体",description:"通过候选审核或后续自动抽取后会出现实体。",icon:r(vt)},null,8,["icon"]))]),_:1}),a("div",kn,[r(k)?(b(),D(ae,{key:0,title:"当前后端还没有图谱接口",description:"新版前端已经加载，但运行容器仍是旧后端。请重建/重启 Bot 后再查看图谱关系。",icon:r(ge)},null,8,["icon"])):Ce("",!0),!r(k)&&r(me).length?(b(),D(Re,{key:1,type:"warning","show-icon":!1,class:"graph-scope-risk"},{default:d(()=>[a("div",Cn,[a("strong",null,"发现 "+u(r(me).length)+" 条历史全局事实需要复核",1),e[35]||(e[35]=a("span",null,"这些事实带有记忆卡片证据，但缺少用户/群作用域，可能来自旧版本迁移。确认不该全局可见时，请回滚事实。",-1))]),a("div",Sn,[(b(!0),T(ie,null,be(r(me).slice(0,5),n=>(b(),T("div",{key:`risk-${n.fact_id}`,class:"graph-scope-risk__item"},[a("span",null,u(n.subject)+" "+u(n.predicate)+" "+u(n.object),1),c(K,{size:"tiny",secondary:"",type:"warning",loading:r(se)===n.fact_id,onClick:j=>Qe(n)},{default:d(()=>[...e[36]||(e[36]=[z(" 回滚 ",-1)])]),_:1},8,["loading","onClick"])]))),128))])]),_:1})):Ce("",!0),(b(!0),T(ie,null,be(r(he),n=>(b(),D(Y,{key:n.fact_id,bordered:"",embedded:"",class:"relationship-card"},{default:d(()=>[a("div",$n,[a("strong",null,u(n.subject),1),a("span",null,u(n.predicate),1),a("strong",null,u(n.object),1)]),a("p",Tn,u(rt(n)),1),a("div",Rn,[c(h,{round:"",size:"small",type:"success"},{default:d(()=>[z(u(m(n.confidence)),1)]),_:2},1024),a("span",null,u(n.source),1),a("span",null,u(st(n)),1),a("span",null,u(n.fact_id),1),n.supersedes?(b(),T("span",zn,"取代 "+u(n.supersedes),1)):Ce("",!0)]),r(X)[n.fact_id]?(b(),T("div",Pn,[a("div",Bn,[c(le,{value:r(Pe)[n.fact_id],"onUpdate:value":j=>r(Pe)[n.fact_id]=j,clearable:"",placeholder:"回滚备注，可选"},null,8,["value","onUpdate:value"]),c(K,{secondary:"",type:"warning",loading:r(se)===n.fact_id,onClick:j=>Qe(n)},{default:d(()=>[...e[37]||(e[37]=[z(" 回滚事实 ",-1)])]),_:1},8,["loading","onClick"])]),a("div",jn,[c(le,{value:r(X)[n.fact_id].subject,"onUpdate:value":j=>r(X)[n.fact_id].subject=j,placeholder:"主体"},null,8,["value","onUpdate:value"]),c(le,{value:r(X)[n.fact_id].predicate,"onUpdate:value":j=>r(X)[n.fact_id].predicate=j,placeholder:"关系"},null,8,["value","onUpdate:value"]),c(le,{value:r(X)[n.fact_id].object,"onUpdate:value":j=>r(X)[n.fact_id].object=j,placeholder:"客体"},null,8,["value","onUpdate:value"]),c(le,{value:r(X)[n.fact_id].note,"onUpdate:value":j=>r(X)[n.fact_id].note=j,placeholder:"取代说明，可选"},null,8,["value","onUpdate:value"]),c(K,{type:"primary",secondary:"",loading:r(se)===n.fact_id,onClick:j=>Ye(n)},{default:d(()=>[...e[38]||(e[38]=[z(" 取代事实 ",-1)])]),_:1},8,["loading","onClick"])])])):Ce("",!0)]),_:2},1024))),128)),!r(k)&&r(he).length===0?(b(),D(ae,{key:2,title:"暂无图谱事实",description:"当前图谱底座已就绪，但还没有 active fact。",icon:r(Ct)},null,8,["icon"])):Ce("",!0)])])]),_:1},8,["show"])]),_:1}),c(ke,{name:"candidates",tab:"候选队列"},{default:d(()=>[c(We,{class:"mb-16"},{left:d(()=>[...e[39]||(e[39]=[a("span",{class:"knowledge-toolbar__title"},"待审核候选",-1),a("span",{class:"knowledge-toolbar__hint"},"中置信事实进入这里，人工通过后才写入图谱。",-1)])]),right:d(()=>[c(K,{secondary:"",loading:r(oe),onClick:de},{default:d(()=>[...e[40]||(e[40]=[z(" 刷新候选 ",-1)])]),_:1},8,["loading"])]),_:1}),c(ze,{show:r(oe)},{default:d(()=>[r(ve).length?(b(),T("div",An,[(b(!0),T(ie,null,be(r(ve),n=>(b(),D(Y,{key:n.candidate_id,bordered:"",embedded:"",class:"candidate-card"},{default:d(()=>[a("div",Wn,[a("div",null,[a("div",En,[a("strong",null,u(n.subject),1),a("span",null,u(n.predicate),1),a("strong",null,u(n.object),1)]),a("p",null,u(ot(n)),1),a("div",Ln,[c(h,{round:"",size:"small",type:"warning"},{default:d(()=>[z(u(m(n.confidence)),1)]),_:2},1024),a("span",null,u(n.source),1),a("span",null,u(n.candidate_id),1)])]),a("div",In,[c(le,{value:r(Ee)[n.candidate_id],"onUpdate:value":j=>r(Ee)[n.candidate_id]=j,clearable:"",placeholder:"拒绝备注，可选"},null,8,["value","onUpdate:value"]),c(Fe,{justify:"end",size:8},{default:d(()=>[c(K,{secondary:"",type:"error",loading:r(ye)===n.candidate_id,onClick:j=>l(n)},{default:d(()=>[...e[41]||(e[41]=[z(" 拒绝 ",-1)])]),_:1},8,["loading","onClick"]),c(K,{type:"primary",loading:r(ye)===n.candidate_id,onClick:j=>s(n)},{default:d(()=>[...e[42]||(e[42]=[z(" 通过 ",-1)])]),_:1},8,["loading","onClick"])]),_:2},1024)])])]),_:2},1024))),128))])):r(k)?(b(),D(ae,{key:1,title:"当前后端还没有图谱候选接口",description:"请重建/重启 Bot，让后端 API 与新版前端保持一致。",icon:r(ge)},null,8,["icon"])):(b(),D(ae,{key:2,title:"没有待审核候选",description:"当前没有中置信图谱候选。后续接入自动抽取后，这里会成为治理入口。",icon:r(ge)},null,8,["icon"]))]),_:1},8,["show"])]),_:1})]),_:1},8,["value"])],64))]),_:1})}}}),Jn=pa(On,[["__scopeId","data-v-d6152ac4"]]);export{Jn as default};
