import{N as fe,p as de,B as Ce,a as Ke,d as Oe,h as ue,r as Ie,c as _e}from"./Popover-Bvp9NSIQ.js";import{b9 as ze,ba as Te,a7 as $e,aW as q,bb as je,bc as De,aX as G,S as ae,o as T,d as O,h as s,q as ve,s as Z,k as w,bd as Ae,c as se,r as X,j as F,z as he,K as Fe,b7 as Be,u as V,be,p as E,F as Me,b0 as Le,bf as Ee,bg as He,bh as Ue,b as P,b3 as We,a as L,g as ce,e as C,f as z,m as qe,v as Ge,x as Ve,bi as Xe,C as re,A as K,aF as A}from"./index-TyYsfcXJ.js";import{c as Ze}from"./create-ref-setter-C4J8sofl.js";function Je(e={},n){const d=De({ctrl:!1,command:!1,win:!1,shift:!1,tab:!1}),{keydown:r,keyup:i}=e,o=a=>{switch(a.key){case"Control":d.ctrl=!0;break;case"Meta":d.command=!0,d.win=!0;break;case"Shift":d.shift=!0;break;case"Tab":d.tab=!0;break}r!==void 0&&Object.keys(r).forEach(m=>{if(m!==a.key)return;const b=r[m];if(typeof b=="function")b(a);else{const{stop:g=!1,prevent:x=!1}=b;g&&a.stopPropagation(),x&&a.preventDefault(),b.handler(a)}})},p=a=>{switch(a.key){case"Control":d.ctrl=!1;break;case"Meta":d.command=!1,d.win=!1;break;case"Shift":d.shift=!1;break;case"Tab":d.tab=!1;break}i!==void 0&&Object.keys(i).forEach(m=>{if(m!==a.key)return;const b=i[m];if(typeof b=="function")b(a);else{const{stop:g=!1,prevent:x=!1}=b;g&&a.stopPropagation(),x&&a.preventDefault(),b.handler(a)}})},v=()=>{(n===void 0||n.value)&&(G("keydown",document,o),G("keyup",document,p)),n!==void 0&&ae(n,a=>{a?(G("keydown",document,o),G("keyup",document,p)):(q("keydown",document,o),q("keyup",document,p))})};return ze()?(Te(v),$e(()=>{(n===void 0||n.value)&&(q("keydown",document,o),q("keyup",document,p))})):v(),je(d)}function Qe(e,n,d){const r=T(e.value);let i=null;return ae(e,o=>{i!==null&&window.clearTimeout(i),o===!0?d&&!d.value?r.value=!0:i=window.setTimeout(()=>{r.value=!0},n):r.value=!1}),r}const Ye=O({name:"ChevronRight",render(){return s("svg",{viewBox:"0 0 16 16",fill:"none",xmlns:"http://www.w3.org/2000/svg"},s("path",{d:"M5.64645 3.14645C5.45118 3.34171 5.45118 3.65829 5.64645 3.85355L9.79289 8L5.64645 12.1464C5.45118 12.3417 5.45118 12.6583 5.64645 12.8536C5.84171 13.0488 6.15829 13.0488 6.35355 12.8536L10.8536 8.35355C11.0488 8.15829 11.0488 7.84171 10.8536 7.64645L6.35355 3.14645C6.15829 2.95118 5.84171 2.95118 5.64645 3.14645Z",fill:"currentColor"}))}}),eo=Object.assign(Object.assign({},de),Z.props),vo=O({name:"Tooltip",props:eo,slots:Object,__popover__:!0,setup(e){const{mergedClsPrefixRef:n}=ve(e),d=Z("Tooltip","-tooltip",void 0,Ae,e,n),r=T(null);return Object.assign(Object.assign({},{syncPosition(){r.value.syncPosition()},setShow(o){r.value.setShow(o)}}),{popoverRef:r,mergedTheme:d,popoverThemeOverrides:w(()=>d.value.self)})},render(){const{mergedTheme:e,internalExtraClass:n}=this;return s(fe,Object.assign(Object.assign({},this.$props),{theme:e.peers.Popover,themeOverrides:e.peerOverrides.Popover,builtinThemeOverrides:this.popoverThemeOverrides,internalExtraClass:n.concat("tooltip"),ref:"popoverRef"}),this.$slots)}}),le=se("n-dropdown-menu"),J=se("n-dropdown"),pe=se("n-dropdown-option"),we=O({name:"DropdownDivider",props:{clsPrefix:{type:String,required:!0}},render(){return s("div",{class:`${this.clsPrefix}-dropdown-divider`})}}),oo=O({name:"DropdownGroupHeader",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0}},setup(){const{showIconRef:e,hasSubmenuRef:n}=F(le),{renderLabelRef:d,labelFieldRef:r,nodePropsRef:i,renderOptionRef:o}=F(J);return{labelField:r,showIcon:e,hasSubmenu:n,renderLabel:d,nodeProps:i,renderOption:o}},render(){var e;const{clsPrefix:n,hasSubmenu:d,showIcon:r,nodeProps:i,renderLabel:o,renderOption:p}=this,{rawNode:v}=this.tmNode,a=s("div",Object.assign({class:`${n}-dropdown-option`},i==null?void 0:i(v)),s("div",{class:`${n}-dropdown-option-body ${n}-dropdown-option-body--group`},s("div",{"data-dropdown-option":!0,class:[`${n}-dropdown-option-body__prefix`,r&&`${n}-dropdown-option-body__prefix--show-icon`]},X(v.icon)),s("div",{class:`${n}-dropdown-option-body__label`,"data-dropdown-option":!0},o?o(v):X((e=v.title)!==null&&e!==void 0?e:v[this.labelField])),s("div",{class:[`${n}-dropdown-option-body__suffix`,d&&`${n}-dropdown-option-body__suffix--has-submenu`],"data-dropdown-option":!0})));return p?p({node:a,option:v}):a}});function ie(e,n){return e.type==="submenu"||e.type===void 0&&e[n]!==void 0}function no(e){return e.type==="group"}function me(e){return e.type==="divider"}function to(e){return e.type==="render"}const ye=O({name:"DropdownOption",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0},parentKey:{type:[String,Number],default:null},placement:{type:String,default:"right-start"},props:Object,scrollable:Boolean},setup(e){const n=F(J),{hoverKeyRef:d,keyboardKeyRef:r,lastToggledSubmenuKeyRef:i,pendingKeyPathRef:o,activeKeyPathRef:p,animatedRef:v,mergedShowRef:a,renderLabelRef:m,renderIconRef:b,labelFieldRef:g,childrenFieldRef:x,renderOptionRef:k,nodePropsRef:N,menuPropsRef:$}=n,S=F(pe,null),I=F(le),_=F(be),U=w(()=>e.tmNode.rawNode),H=w(()=>{const{value:t}=x;return ie(e.tmNode.rawNode,t)}),Q=w(()=>{const{disabled:t}=e.tmNode;return t}),Y=w(()=>{if(!H.value)return!1;const{key:t,disabled:c}=e.tmNode;if(c)return!1;const{value:y}=d,{value:j}=r,{value:te}=i,{value:D}=o;return y!==null?D.includes(t):j!==null?D.includes(t)&&D[D.length-1]!==t:te!==null?D.includes(t):!1}),ee=w(()=>r.value===null&&!v.value),oe=Qe(Y,300,ee),ne=w(()=>!!(S!=null&&S.enteringSubmenuRef.value)),B=T(!1);E(pe,{enteringSubmenuRef:B});function M(){B.value=!0}function W(){B.value=!1}function R(){const{parentKey:t,tmNode:c}=e;c.disabled||a.value&&(i.value=t,r.value=null,d.value=c.key)}function l(){const{tmNode:t}=e;t.disabled||a.value&&d.value!==t.key&&R()}function u(t){if(e.tmNode.disabled||!a.value)return;const{relatedTarget:c}=t;c&&!ue({target:c},"dropdownOption")&&!ue({target:c},"scrollbarRail")&&(d.value=null)}function f(){const{value:t}=H,{tmNode:c}=e;a.value&&!t&&!c.disabled&&(n.doSelect(c.key,c.rawNode),n.doUpdateShow(!1))}return{labelField:g,renderLabel:m,renderIcon:b,siblingHasIcon:I.showIconRef,siblingHasSubmenu:I.hasSubmenuRef,menuProps:$,popoverBody:_,animated:v,mergedShowSubmenu:w(()=>oe.value&&!ne.value),rawNode:U,hasSubmenu:H,pending:V(()=>{const{value:t}=o,{key:c}=e.tmNode;return t.includes(c)}),childActive:V(()=>{const{value:t}=p,{key:c}=e.tmNode,y=t.findIndex(j=>c===j);return y===-1?!1:y<t.length-1}),active:V(()=>{const{value:t}=p,{key:c}=e.tmNode,y=t.findIndex(j=>c===j);return y===-1?!1:y===t.length-1}),mergedDisabled:Q,renderOption:k,nodeProps:N,handleClick:f,handleMouseMove:l,handleMouseEnter:R,handleMouseLeave:u,handleSubmenuBeforeEnter:M,handleSubmenuAfterEnter:W}},render(){var e,n;const{animated:d,rawNode:r,mergedShowSubmenu:i,clsPrefix:o,siblingHasIcon:p,siblingHasSubmenu:v,renderLabel:a,renderIcon:m,renderOption:b,nodeProps:g,props:x,scrollable:k}=this;let N=null;if(i){const _=(e=this.menuProps)===null||e===void 0?void 0:e.call(this,r,r.children);N=s(ge,Object.assign({},_,{clsPrefix:o,scrollable:this.scrollable,tmNodes:this.tmNode.children,parentKey:this.tmNode.key}))}const $={class:[`${o}-dropdown-option-body`,this.pending&&`${o}-dropdown-option-body--pending`,this.active&&`${o}-dropdown-option-body--active`,this.childActive&&`${o}-dropdown-option-body--child-active`,this.mergedDisabled&&`${o}-dropdown-option-body--disabled`],onMousemove:this.handleMouseMove,onMouseenter:this.handleMouseEnter,onMouseleave:this.handleMouseLeave,onClick:this.handleClick},S=g==null?void 0:g(r),I=s("div",Object.assign({class:[`${o}-dropdown-option`,S==null?void 0:S.class],"data-dropdown-option":!0},S),s("div",he($,x),[s("div",{class:[`${o}-dropdown-option-body__prefix`,p&&`${o}-dropdown-option-body__prefix--show-icon`]},[m?m(r):X(r.icon)]),s("div",{"data-dropdown-option":!0,class:`${o}-dropdown-option-body__label`},a?a(r):X((n=r[this.labelField])!==null&&n!==void 0?n:r.title)),s("div",{"data-dropdown-option":!0,class:[`${o}-dropdown-option-body__suffix`,v&&`${o}-dropdown-option-body__suffix--has-submenu`]},this.hasSubmenu?s(Fe,null,{default:()=>s(Ye,null)}):null)]),this.hasSubmenu?s(Ce,null,{default:()=>[s(Ke,null,{default:()=>s("div",{class:`${o}-dropdown-offset-container`},s(Oe,{show:this.mergedShowSubmenu,placement:this.placement,to:k&&this.popoverBody||void 0,teleportDisabled:!k},{default:()=>s("div",{class:`${o}-dropdown-menu-wrapper`},d?s(Be,{onBeforeEnter:this.handleSubmenuBeforeEnter,onAfterEnter:this.handleSubmenuAfterEnter,name:"fade-in-scale-up-transition",appear:!0},{default:()=>N}):N)}))})]}):null);return b?b({node:I,option:r}):I}}),ro=O({name:"NDropdownGroup",props:{clsPrefix:{type:String,required:!0},tmNode:{type:Object,required:!0},parentKey:{type:[String,Number],default:null}},render(){const{tmNode:e,parentKey:n,clsPrefix:d}=this,{children:r}=e;return s(Me,null,s(oo,{clsPrefix:d,tmNode:e,key:e.key}),r==null?void 0:r.map(i=>{const{rawNode:o}=i;return o.show===!1?null:me(o)?s(we,{clsPrefix:d,key:i.key}):i.isGroup?(Le("dropdown","`group` node is not allowed to be put in `group` node."),null):s(ye,{clsPrefix:d,tmNode:i,parentKey:n,key:i.key})}))}}),io=O({name:"DropdownRenderOption",props:{tmNode:{type:Object,required:!0}},render(){const{rawNode:{render:e,props:n}}=this.tmNode;return s("div",n,[e==null?void 0:e()])}}),ge=O({name:"DropdownMenu",props:{scrollable:Boolean,showArrow:Boolean,arrowStyle:[String,Object],clsPrefix:{type:String,required:!0},tmNodes:{type:Array,default:()=>[]},parentKey:{type:[String,Number],default:null}},setup(e){const{renderIconRef:n,childrenFieldRef:d}=F(J);E(le,{showIconRef:w(()=>{const i=n.value;return e.tmNodes.some(o=>{var p;if(o.isGroup)return(p=o.children)===null||p===void 0?void 0:p.some(({rawNode:a})=>i?i(a):a.icon);const{rawNode:v}=o;return i?i(v):v.icon})}),hasSubmenuRef:w(()=>{const{value:i}=d;return e.tmNodes.some(o=>{var p;if(o.isGroup)return(p=o.children)===null||p===void 0?void 0:p.some(({rawNode:a})=>ie(a,i));const{rawNode:v}=o;return ie(v,i)})})});const r=T(null);return E(He,null),E(Ue,null),E(be,r),{bodyRef:r}},render(){const{parentKey:e,clsPrefix:n,scrollable:d}=this,r=this.tmNodes.map(i=>{const{rawNode:o}=i;return o.show===!1?null:to(o)?s(io,{tmNode:i,key:i.key}):me(o)?s(we,{clsPrefix:n,key:i.key}):no(o)?s(ro,{clsPrefix:n,tmNode:i,parentKey:e,key:i.key}):s(ye,{clsPrefix:n,tmNode:i,parentKey:e,key:i.key,props:o.props,scrollable:d})});return s("div",{class:[`${n}-dropdown-menu`,d&&`${n}-dropdown-menu--scrollable`],ref:"bodyRef"},d?s(Ee,{contentClass:`${n}-dropdown-menu__content`},{default:()=>r}):r,this.showArrow?Ie({clsPrefix:n,arrowStyle:this.arrowStyle,arrowClass:void 0,arrowWrapperClass:void 0,arrowWrapperStyle:void 0}):null)}}),ao=P("dropdown-menu",`
 transform-origin: var(--v-transform-origin);
 background-color: var(--n-color);
 border-radius: var(--n-border-radius);
 box-shadow: var(--n-box-shadow);
 position: relative;
 transition:
 background-color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier);
`,[We(),P("dropdown-option",`
 position: relative;
 `,[L("a",`
 text-decoration: none;
 color: inherit;
 outline: none;
 `,[L("&::before",`
 content: "";
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 `)]),P("dropdown-option-body",`
 display: flex;
 cursor: pointer;
 position: relative;
 height: var(--n-option-height);
 line-height: var(--n-option-height);
 font-size: var(--n-font-size);
 color: var(--n-option-text-color);
 transition: color .3s var(--n-bezier);
 `,[L("&::before",`
 content: "";
 position: absolute;
 top: 0;
 bottom: 0;
 left: 4px;
 right: 4px;
 transition: background-color .3s var(--n-bezier);
 border-radius: var(--n-border-radius);
 `),ce("disabled",[C("pending",`
 color: var(--n-option-text-color-hover);
 `,[z("prefix, suffix",`
 color: var(--n-option-text-color-hover);
 `),L("&::before","background-color: var(--n-option-color-hover);")]),C("active",`
 color: var(--n-option-text-color-active);
 `,[z("prefix, suffix",`
 color: var(--n-option-text-color-active);
 `),L("&::before","background-color: var(--n-option-color-active);")]),C("child-active",`
 color: var(--n-option-text-color-child-active);
 `,[z("prefix, suffix",`
 color: var(--n-option-text-color-child-active);
 `)])]),C("disabled",`
 cursor: not-allowed;
 opacity: var(--n-option-opacity-disabled);
 `),C("group",`
 font-size: calc(var(--n-font-size) - 1px);
 color: var(--n-group-header-text-color);
 `,[z("prefix",`
 width: calc(var(--n-option-prefix-width) / 2);
 `,[C("show-icon",`
 width: calc(var(--n-option-icon-prefix-width) / 2);
 `)])]),z("prefix",`
 width: var(--n-option-prefix-width);
 display: flex;
 justify-content: center;
 align-items: center;
 color: var(--n-prefix-color);
 transition: color .3s var(--n-bezier);
 z-index: 1;
 `,[C("show-icon",`
 width: var(--n-option-icon-prefix-width);
 `),P("icon",`
 font-size: var(--n-option-icon-size);
 `)]),z("label",`
 white-space: nowrap;
 flex: 1;
 z-index: 1;
 `),z("suffix",`
 box-sizing: border-box;
 flex-grow: 0;
 flex-shrink: 0;
 display: flex;
 justify-content: flex-end;
 align-items: center;
 min-width: var(--n-option-suffix-width);
 padding: 0 8px;
 transition: color .3s var(--n-bezier);
 color: var(--n-suffix-color);
 z-index: 1;
 `,[C("has-submenu",`
 width: var(--n-option-icon-suffix-width);
 `),P("icon",`
 font-size: var(--n-option-icon-size);
 `)]),P("dropdown-menu","pointer-events: all;")]),P("dropdown-offset-container",`
 pointer-events: none;
 position: absolute;
 left: 0;
 right: 0;
 top: -4px;
 bottom: -4px;
 `)]),P("dropdown-divider",`
 transition: background-color .3s var(--n-bezier);
 background-color: var(--n-divider-color);
 height: 1px;
 margin: 4px 0;
 `),P("dropdown-menu-wrapper",`
 transform-origin: var(--v-transform-origin);
 width: fit-content;
 `),L(">",[P("scrollbar",`
 height: inherit;
 max-height: inherit;
 `)]),ce("scrollable",`
 padding: var(--n-padding);
 `),C("scrollable",[z("content",`
 padding: var(--n-padding);
 `)])]),so={animated:{type:Boolean,default:!0},keyboard:{type:Boolean,default:!0},size:String,inverted:Boolean,placement:{type:String,default:"bottom"},onSelect:[Function,Array],options:{type:Array,default:()=>[]},menuProps:Function,showArrow:Boolean,renderLabel:Function,renderIcon:Function,renderOption:Function,nodeProps:Function,labelField:{type:String,default:"label"},keyField:{type:String,default:"key"},childrenField:{type:String,default:"children"},value:[String,Number]},lo=Object.keys(de),uo=Object.assign(Object.assign(Object.assign({},de),so),Z.props),ho=O({name:"Dropdown",inheritAttrs:!1,props:uo,setup(e){const n=T(!1),d=Ge(K(e,"show"),n),r=w(()=>{const{keyField:l,childrenField:u}=e;return _e(e.options,{getKey(f){return f[l]},getDisabled(f){return f.disabled===!0},getIgnored(f){return f.type==="divider"||f.type==="render"},getChildren(f){return f[u]}})}),i=w(()=>r.value.treeNodes),o=T(null),p=T(null),v=T(null),a=w(()=>{var l,u,f;return(f=(u=(l=o.value)!==null&&l!==void 0?l:p.value)!==null&&u!==void 0?u:v.value)!==null&&f!==void 0?f:null}),m=w(()=>r.value.getPath(a.value).keyPath),b=w(()=>r.value.getPath(e.value).keyPath),g=V(()=>e.keyboard&&d.value);Je({keydown:{ArrowUp:{prevent:!0,handler:ee},ArrowRight:{prevent:!0,handler:Y},ArrowDown:{prevent:!0,handler:oe},ArrowLeft:{prevent:!0,handler:Q},Enter:{prevent:!0,handler:ne},Escape:H}},g);const{mergedClsPrefixRef:x,inlineThemeDisabled:k,mergedComponentPropsRef:N}=ve(e),$=w(()=>{var l,u;return e.size||((u=(l=N==null?void 0:N.value)===null||l===void 0?void 0:l.Dropdown)===null||u===void 0?void 0:u.size)||"medium"}),S=Z("Dropdown","-dropdown",ao,Xe,e,x);E(J,{labelFieldRef:K(e,"labelField"),childrenFieldRef:K(e,"childrenField"),renderLabelRef:K(e,"renderLabel"),renderIconRef:K(e,"renderIcon"),hoverKeyRef:o,keyboardKeyRef:p,lastToggledSubmenuKeyRef:v,pendingKeyPathRef:m,activeKeyPathRef:b,animatedRef:K(e,"animated"),mergedShowRef:d,nodePropsRef:K(e,"nodeProps"),renderOptionRef:K(e,"renderOption"),menuPropsRef:K(e,"menuProps"),doSelect:I,doUpdateShow:_}),ae(d,l=>{!e.animated&&!l&&U()});function I(l,u){const{onSelect:f}=e;f&&re(f,l,u)}function _(l){const{"onUpdate:show":u,onUpdateShow:f}=e;u&&re(u,l),f&&re(f,l),n.value=l}function U(){o.value=null,p.value=null,v.value=null}function H(){_(!1)}function Q(){M("left")}function Y(){M("right")}function ee(){M("up")}function oe(){M("down")}function ne(){const l=B();l!=null&&l.isLeaf&&d.value&&(I(l.key,l.rawNode),_(!1))}function B(){var l;const{value:u}=r,{value:f}=a;return!u||f===null?null:(l=u.getNode(f))!==null&&l!==void 0?l:null}function M(l){const{value:u}=a,{value:{getFirstAvailableNode:f}}=r;let t=null;if(u===null){const c=f();c!==null&&(t=c.key)}else{const c=B();if(c){let y;switch(l){case"down":y=c.getNext();break;case"up":y=c.getPrev();break;case"right":y=c.getChild();break;case"left":y=c.getParent();break}y&&(t=y.key)}}t!==null&&(o.value=null,p.value=t)}const W=w(()=>{const{inverted:l}=e,u=$.value,{common:{cubicBezierEaseInOut:f},self:t}=S.value,{padding:c,dividerColor:y,borderRadius:j,optionOpacityDisabled:te,[A("optionIconSuffixWidth",u)]:D,[A("optionSuffixWidth",u)]:xe,[A("optionIconPrefixWidth",u)]:Se,[A("optionPrefixWidth",u)]:Pe,[A("fontSize",u)]:ke,[A("optionHeight",u)]:Ne,[A("optionIconSize",u)]:Re}=t,h={"--n-bezier":f,"--n-font-size":ke,"--n-padding":c,"--n-border-radius":j,"--n-option-height":Ne,"--n-option-prefix-width":Pe,"--n-option-icon-prefix-width":Se,"--n-option-suffix-width":xe,"--n-option-icon-suffix-width":D,"--n-option-icon-size":Re,"--n-divider-color":y,"--n-option-opacity-disabled":te};return l?(h["--n-color"]=t.colorInverted,h["--n-option-color-hover"]=t.optionColorHoverInverted,h["--n-option-color-active"]=t.optionColorActiveInverted,h["--n-option-text-color"]=t.optionTextColorInverted,h["--n-option-text-color-hover"]=t.optionTextColorHoverInverted,h["--n-option-text-color-active"]=t.optionTextColorActiveInverted,h["--n-option-text-color-child-active"]=t.optionTextColorChildActiveInverted,h["--n-prefix-color"]=t.prefixColorInverted,h["--n-suffix-color"]=t.suffixColorInverted,h["--n-group-header-text-color"]=t.groupHeaderTextColorInverted):(h["--n-color"]=t.color,h["--n-option-color-hover"]=t.optionColorHover,h["--n-option-color-active"]=t.optionColorActive,h["--n-option-text-color"]=t.optionTextColor,h["--n-option-text-color-hover"]=t.optionTextColorHover,h["--n-option-text-color-active"]=t.optionTextColorActive,h["--n-option-text-color-child-active"]=t.optionTextColorChildActive,h["--n-prefix-color"]=t.prefixColor,h["--n-suffix-color"]=t.suffixColor,h["--n-group-header-text-color"]=t.groupHeaderTextColor),h}),R=k?Ve("dropdown",w(()=>`${$.value[0]}${e.inverted?"i":""}`),W,e):void 0;return{mergedClsPrefix:x,mergedTheme:S,mergedSize:$,tmNodes:i,mergedShow:d,handleAfterLeave:()=>{e.animated&&U()},doUpdateShow:_,cssVars:k?void 0:W,themeClass:R==null?void 0:R.themeClass,onRender:R==null?void 0:R.onRender}},render(){const e=(r,i,o,p,v)=>{var a;const{mergedClsPrefix:m,menuProps:b}=this;(a=this.onRender)===null||a===void 0||a.call(this);const g=(b==null?void 0:b(void 0,this.tmNodes.map(k=>k.rawNode)))||{},x={ref:Ze(i),class:[r,`${m}-dropdown`,`${m}-dropdown--${this.mergedSize}-size`,this.themeClass],clsPrefix:m,tmNodes:this.tmNodes,style:[...o,this.cssVars],showArrow:this.showArrow,arrowStyle:this.arrowStyle,scrollable:this.scrollable,onMouseenter:p,onMouseleave:v};return s(ge,he(this.$attrs,x,g))},{mergedTheme:n}=this,d={show:this.mergedShow,theme:n.peers.Popover,themeOverrides:n.peerOverrides.Popover,internalOnAfterLeave:this.handleAfterLeave,internalRenderBody:e,onUpdateShow:this.doUpdateShow,"onUpdate:show":void 0};return s(fe,Object.assign({},qe(this.$props,lo),d),{trigger:()=>{var r,i;return(i=(r=this.$slots).default)===null||i===void 0?void 0:i.call(r)}})}});export{Ye as C,vo as N,ho as a};
