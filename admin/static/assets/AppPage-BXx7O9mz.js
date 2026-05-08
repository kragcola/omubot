import{bq as Ce,cG as pe,bt as l,b as me,e as f,f as _,g as H,a as R,d as Z,ap as q,h as x,az as fe,q as ke,s as Q,aI as _e,x as xe,o as ye,C as Pe,k as P,aF as h,aG as Ie,cH as J,p as ze,A as Se,c as Be,E as I,J as $e,_ as He,M as Y,ag as w,G as p,Z as Re,a5 as z,aa as L,L as S,a3 as we,Y as Me,D as y,a6 as Te}from"./index-TyYsfcXJ.js";function Ee(a){const{textColor2:d,primaryColorHover:r,primaryColorPressed:b,primaryColor:t,infoColor:i,successColor:n,warningColor:o,errorColor:s,baseColor:u,borderColor:k,opacityDisabled:v,tagColor:M,closeIconColor:B,closeIconColorHover:m,closeIconColorPressed:e,borderRadiusSmall:c,fontSizeMini:C,fontSizeTiny:g,fontSizeSmall:T,fontSizeMedium:E,heightMini:O,heightTiny:W,heightSmall:j,heightMedium:F,closeColorHover:A,closeColorPressed:D,buttonColor2Hover:N,buttonColor2Pressed:V,fontWeightStrong:U}=a;return Object.assign(Object.assign({},pe),{closeBorderRadius:c,heightTiny:O,heightSmall:W,heightMedium:j,heightLarge:F,borderRadius:c,opacityDisabled:v,fontSizeTiny:C,fontSizeSmall:g,fontSizeMedium:T,fontSizeLarge:E,fontWeightStrong:U,textColorCheckable:d,textColorHoverCheckable:d,textColorPressedCheckable:d,textColorChecked:u,colorCheckable:"#0000",colorHoverCheckable:N,colorPressedCheckable:V,colorChecked:t,colorCheckedHover:r,colorCheckedPressed:b,border:`1px solid ${k}`,textColor:d,color:M,colorBordered:"rgb(250, 250, 252)",closeIconColor:B,closeIconColorHover:m,closeIconColorPressed:e,closeColorHover:A,closeColorPressed:D,borderPrimary:`1px solid ${l(t,{alpha:.3})}`,textColorPrimary:t,colorPrimary:l(t,{alpha:.12}),colorBorderedPrimary:l(t,{alpha:.1}),closeIconColorPrimary:t,closeIconColorHoverPrimary:t,closeIconColorPressedPrimary:t,closeColorHoverPrimary:l(t,{alpha:.12}),closeColorPressedPrimary:l(t,{alpha:.18}),borderInfo:`1px solid ${l(i,{alpha:.3})}`,textColorInfo:i,colorInfo:l(i,{alpha:.12}),colorBorderedInfo:l(i,{alpha:.1}),closeIconColorInfo:i,closeIconColorHoverInfo:i,closeIconColorPressedInfo:i,closeColorHoverInfo:l(i,{alpha:.12}),closeColorPressedInfo:l(i,{alpha:.18}),borderSuccess:`1px solid ${l(n,{alpha:.3})}`,textColorSuccess:n,colorSuccess:l(n,{alpha:.12}),colorBorderedSuccess:l(n,{alpha:.1}),closeIconColorSuccess:n,closeIconColorHoverSuccess:n,closeIconColorPressedSuccess:n,closeColorHoverSuccess:l(n,{alpha:.12}),closeColorPressedSuccess:l(n,{alpha:.18}),borderWarning:`1px solid ${l(o,{alpha:.35})}`,textColorWarning:o,colorWarning:l(o,{alpha:.15}),colorBorderedWarning:l(o,{alpha:.12}),closeIconColorWarning:o,closeIconColorHoverWarning:o,closeIconColorPressedWarning:o,closeColorHoverWarning:l(o,{alpha:.12}),closeColorPressedWarning:l(o,{alpha:.18}),borderError:`1px solid ${l(s,{alpha:.23})}`,textColorError:s,colorError:l(s,{alpha:.1}),colorBorderedError:l(s,{alpha:.08}),closeIconColorError:s,closeIconColorHoverError:s,closeIconColorPressedError:s,closeColorHoverError:l(s,{alpha:.12}),closeColorPressedError:l(s,{alpha:.18})})}const Oe={name:"Tag",common:Ce,self:Ee},We={color:Object,type:{type:String,default:"default"},round:Boolean,size:String,closable:Boolean,disabled:{type:Boolean,default:void 0}},je=me("tag",`
 --n-close-margin: var(--n-close-margin-top) var(--n-close-margin-right) var(--n-close-margin-bottom) var(--n-close-margin-left);
 white-space: nowrap;
 position: relative;
 box-sizing: border-box;
 cursor: default;
 display: inline-flex;
 align-items: center;
 flex-wrap: nowrap;
 padding: var(--n-padding);
 border-radius: var(--n-border-radius);
 color: var(--n-text-color);
 background-color: var(--n-color);
 transition: 
 border-color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier),
 box-shadow .3s var(--n-bezier),
 opacity .3s var(--n-bezier);
 line-height: 1;
 height: var(--n-height);
 font-size: var(--n-font-size);
`,[f("strong",`
 font-weight: var(--n-font-weight-strong);
 `),_("border",`
 pointer-events: none;
 position: absolute;
 left: 0;
 right: 0;
 top: 0;
 bottom: 0;
 border-radius: inherit;
 border: var(--n-border);
 transition: border-color .3s var(--n-bezier);
 `),_("icon",`
 display: flex;
 margin: 0 4px 0 0;
 color: var(--n-text-color);
 transition: color .3s var(--n-bezier);
 font-size: var(--n-avatar-size-override);
 `),_("avatar",`
 display: flex;
 margin: 0 6px 0 0;
 `),_("close",`
 margin: var(--n-close-margin);
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),f("round",`
 padding: 0 calc(var(--n-height) / 3);
 border-radius: calc(var(--n-height) / 2);
 `,[_("icon",`
 margin: 0 4px 0 calc((var(--n-height) - 8px) / -2);
 `),_("avatar",`
 margin: 0 6px 0 calc((var(--n-height) - 8px) / -2);
 `),f("closable",`
 padding: 0 calc(var(--n-height) / 4) 0 calc(var(--n-height) / 3);
 `)]),f("icon, avatar",[f("round",`
 padding: 0 calc(var(--n-height) / 3) 0 calc(var(--n-height) / 2);
 `)]),f("disabled",`
 cursor: not-allowed !important;
 opacity: var(--n-opacity-disabled);
 `),f("checkable",`
 cursor: pointer;
 box-shadow: none;
 color: var(--n-text-color-checkable);
 background-color: var(--n-color-checkable);
 `,[H("disabled",[R("&:hover","background-color: var(--n-color-hover-checkable);",[H("checked","color: var(--n-text-color-hover-checkable);")]),R("&:active","background-color: var(--n-color-pressed-checkable);",[H("checked","color: var(--n-text-color-pressed-checkable);")])]),f("checked",`
 color: var(--n-text-color-checked);
 background-color: var(--n-color-checked);
 `,[H("disabled",[R("&:hover","background-color: var(--n-color-checked-hover);"),R("&:active","background-color: var(--n-color-checked-pressed);")])])])]),Fe=Object.assign(Object.assign(Object.assign({},Q.props),We),{bordered:{type:Boolean,default:void 0},checked:Boolean,checkable:Boolean,strong:Boolean,triggerClickOnClose:Boolean,onClose:[Array,Function],onMouseenter:Function,onMouseleave:Function,"onUpdate:checked":Function,onUpdateChecked:Function,internalCloseFocusable:{type:Boolean,default:!0},internalCloseIsButtonTag:{type:Boolean,default:!0},onCheckedChange:Function}),Ae=Be("n-tag"),eo=Z({name:"Tag",props:Fe,slots:Object,setup(a){const d=ye(null),{mergedBorderedRef:r,mergedClsPrefixRef:b,inlineThemeDisabled:t,mergedRtlRef:i,mergedComponentPropsRef:n}=ke(a),o=P(()=>{var e,c;return a.size||((c=(e=n==null?void 0:n.value)===null||e===void 0?void 0:e.Tag)===null||c===void 0?void 0:c.size)||"medium"}),s=Q("Tag","-tag",je,Oe,a,b);ze(Ae,{roundRef:Se(a,"round")});function u(){if(!a.disabled&&a.checkable){const{checked:e,onCheckedChange:c,onUpdateChecked:C,"onUpdate:checked":g}=a;C&&C(!e),g&&g(!e),c&&c(!e)}}function k(e){if(a.triggerClickOnClose||e.stopPropagation(),!a.disabled){const{onClose:c}=a;c&&Pe(c,e)}}const v={setTextContent(e){const{value:c}=d;c&&(c.textContent=e)}},M=_e("Tag",i,b),B=P(()=>{const{type:e,color:{color:c,textColor:C}={}}=a,g=o.value,{common:{cubicBezierEaseInOut:T},self:{padding:E,closeMargin:O,borderRadius:W,opacityDisabled:j,textColorCheckable:F,textColorHoverCheckable:A,textColorPressedCheckable:D,textColorChecked:N,colorCheckable:V,colorHoverCheckable:U,colorPressedCheckable:X,colorChecked:ee,colorCheckedHover:oe,colorCheckedPressed:re,closeBorderRadius:le,fontWeightStrong:ae,[h("colorBordered",e)]:se,[h("closeSize",g)]:ce,[h("closeIconSize",g)]:te,[h("fontSize",g)]:ne,[h("height",g)]:G,[h("color",e)]:ie,[h("textColor",e)]:de,[h("border",e)]:he,[h("closeIconColor",e)]:K,[h("closeIconColorHover",e)]:ge,[h("closeIconColorPressed",e)]:be,[h("closeColorHover",e)]:ue,[h("closeColorPressed",e)]:ve}}=s.value,$=Ie(O);return{"--n-font-weight-strong":ae,"--n-avatar-size-override":`calc(${G} - 8px)`,"--n-bezier":T,"--n-border-radius":W,"--n-border":he,"--n-close-icon-size":te,"--n-close-color-pressed":ve,"--n-close-color-hover":ue,"--n-close-border-radius":le,"--n-close-icon-color":K,"--n-close-icon-color-hover":ge,"--n-close-icon-color-pressed":be,"--n-close-icon-color-disabled":K,"--n-close-margin-top":$.top,"--n-close-margin-right":$.right,"--n-close-margin-bottom":$.bottom,"--n-close-margin-left":$.left,"--n-close-size":ce,"--n-color":c||(r.value?se:ie),"--n-color-checkable":V,"--n-color-checked":ee,"--n-color-checked-hover":oe,"--n-color-checked-pressed":re,"--n-color-hover-checkable":U,"--n-color-pressed-checkable":X,"--n-font-size":ne,"--n-height":G,"--n-opacity-disabled":j,"--n-padding":E,"--n-text-color":C||de,"--n-text-color-checkable":F,"--n-text-color-checked":N,"--n-text-color-hover-checkable":A,"--n-text-color-pressed-checkable":D}}),m=t?xe("tag",P(()=>{let e="";const{type:c,color:{color:C,textColor:g}={}}=a;return e+=c[0],e+=o.value[0],C&&(e+=`a${J(C)}`),g&&(e+=`b${J(g)}`),r.value&&(e+="c"),e}),B,a):void 0;return Object.assign(Object.assign({},v),{rtlEnabled:M,mergedClsPrefix:b,contentRef:d,mergedBordered:r,handleClick:u,handleCloseClick:k,cssVars:t?void 0:B,themeClass:m==null?void 0:m.themeClass,onRender:m==null?void 0:m.onRender})},render(){var a,d;const{mergedClsPrefix:r,rtlEnabled:b,closable:t,color:{borderColor:i}={},round:n,onRender:o,$slots:s}=this;o==null||o();const u=q(s.avatar,v=>v&&x("div",{class:`${r}-tag__avatar`},v)),k=q(s.icon,v=>v&&x("div",{class:`${r}-tag__icon`},v));return x("div",{class:[`${r}-tag`,this.themeClass,{[`${r}-tag--rtl`]:b,[`${r}-tag--strong`]:this.strong,[`${r}-tag--disabled`]:this.disabled,[`${r}-tag--checkable`]:this.checkable,[`${r}-tag--checked`]:this.checkable&&this.checked,[`${r}-tag--round`]:n,[`${r}-tag--avatar`]:u,[`${r}-tag--icon`]:k,[`${r}-tag--closable`]:t}],style:this.cssVars,onClick:this.handleClick,onMouseenter:this.onMouseenter,onMouseleave:this.onMouseleave},k||u,x("span",{class:`${r}-tag__content`,ref:"contentRef"},(d=(a=this.$slots).default)===null||d===void 0?void 0:d.call(a)),!this.checkable&&t?x(fe,{clsPrefix:r,class:`${r}-tag__close`,disabled:this.disabled,onClick:this.handleCloseClick,focusable:this.internalCloseFocusable,round:n,isButtonTag:this.internalCloseIsButtonTag,absolute:!0}):null,!this.checkable&&this.mergedBordered?x("div",{class:`${r}-tag__border`,style:{borderColor:i}}):null)}}),De={class:"om-page h-full flex-col flex-1 overflow-hidden"},Ne={key:1,class:"om-page__hero-inner"},Ve={class:"om-page__hero-main"},Ue={class:"om-page__eyebrow"},Le={class:"om-page__title-row"},Ge={class:"om-page__title-stack"},Ke={class:"om-page__title-line"},qe={class:"om-page__title"},Je={key:0,class:"om-page__description"},Ye={key:0,class:"om-page__action"},Ze={"data-page-scroll-root":"",class:"cus-scroll om-page__body mx-12 mb-12 h-0 flex-1 rounded-16"},Qe=Z({__name:"AppPage",props:{back:{type:Boolean},showFooter:{type:Boolean},showHeader:{type:Boolean,default:!0},title:{},description:{},eyebrow:{}},setup(a){const d=a,r=we(),b=Me(),t=P(()=>{var o;return d.title??String(((o=r.meta)==null?void 0:o.title)??"")}),i=P(()=>{var o;return d.description??String(((o=r.meta)==null?void 0:o.description)??"")}),n=P(()=>d.eyebrow??"Omubot Console");return(o,s)=>{const u=He;return y(),I("main",De,[a.showHeader?(y(),$e(u,{key:0,bordered:"",elevated:"",class:"om-page__hero mx-16 mt-16 min-h-60"},{default:Y(()=>[o.$slots.header?z(o.$slots,"header",{key:0},void 0,!0):(y(),I("div",Ne,[p("div",Ve,[p("div",Ue,L(S(n)),1),p("div",Le,[z(o.$slots,"title-prefix",{},()=>[a.back?(y(),I("button",{key:0,type:"button",class:"om-page__back",onClick:s[0]||(s[0]=k=>S(b).back())},[...s[1]||(s[1]=[p("span",null,"←",-1),p("span",null,"返回",-1)])])):w("",!0)],!0),p("div",Ge,[p("div",Ke,[p("h1",qe,L(S(t)),1),z(o.$slots,"title-suffix",{},void 0,!0)]),S(i)?(y(),I("p",Je,L(S(i)),1)):w("",!0)])])]),o.$slots.action?(y(),I("div",Ye,[z(o.$slots,"action",{},void 0,!0)])):w("",!0)]))]),_:3})):w("",!0),p("div",Ze,[Re(u,{bordered:"",elevated:"",class:"om-page__surface min-h-full p-24"},{default:Y(()=>[z(o.$slots,"default",{},void 0,!0)]),_:3})])])}}}),oo=Te(Qe,[["__scopeId","data-v-74d7b83b"]]);export{oo as A,eo as _,We as c,Oe as t};
