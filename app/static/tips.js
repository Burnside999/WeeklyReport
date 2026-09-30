'use strict';
// Short explanations are shared by hover, keyboard focus and touch.
(() => {
  const tips = [
    ['#document-form [name=name]','文档管理器名称','给这张表起一个便于区分的名称；同一帐号内不能重名。'],
    ['#document-form [name=url]','文档 URL','粘贴腾讯在线表格的分享链接。授权帐号必须能打开它；创建后换表需新建管理器。'],
    ['[name=client_id]','Client ID','复制腾讯文档开放平台“开发者信息”里的 client_id（应用ID）。与 Open ID、Access Token 配套使用，同一帐号的文档共用。'],
    ['[name=open_id]','Open ID','复制平台的 open_id，不是 QQ 号。请绑定有权访问目标表格的腾讯文档帐号。'],
    ['[name=access_token]','Access Token','复制平台的 access_token，不要复制星号。到期后在平台重置并更新这里；清空后保存会删除现有凭据。'],
    ['#source-sheet-select','名单工作表','选存放同事姓名的工作表，它可以与周报内容所在的工作表不同。新增工作表后先刷新列表。'],
    ['.distribution-options legend','分布方式','姓名沿一列向下排，选列分布；沿一行向右排，选行分布。切换后起止范围也会改为行或列。'],
    ['#source-form .axis-fields[data-distribution=column] [name=column]','姓名所在列','例如姓名在 A2–A20：填 A，起始行填 2，结束行填 20。不包含标题行。'],
    ['#source-form .axis-fields[data-distribution=row] [name=row]','姓名所在行','例如姓名在 B1–Z1：填 1，起始列填 B，结束列填 Z。'],
    ['#save-selection','统计范围','读取名单后勾选要统计的人，再点保存。全选、全不选只改变勾选，尚未保存不会生效。'],
    ['#rule-form [name=variable_name]','规则变量名','留空自动生成 listener1 等名称。邮件引用依赖这个名称；改名后记得更新模板引用。'],
    ['#sheet-select','监听工作表','选择实际填写周报的工作表，再指定责任人和待检查的区域。'],
    ['#rule-form [name=owner_column]','责任人所在列','填写姓名所在的列，如 A。系统用已选名单的姓名匹配责任人文本，括号内姓名也会匹配。'],
    ['#rule-form [name=owner_row]','责任人所在行','填写姓名所在的行号，如 1。请确认与数据源名单中的姓名写法一致。'],
    ['#rule-form [name=target_columns]','需要检查的列','用英文逗号分隔，如 C,D。单项任务中任意检查格有内容就算已填，不要求全部填满。'],
    ['#rule-form [name=target_rows]','需要检查的行','用英文逗号分隔，如 3,4。责任人的合并区域算一项任务，任意检查格有内容就算已填。'],
    ['#rule-form [name=end_row], #rule-form [name=end_column]','检查范围','起止位置都包含在范围内。避开表头，并覆盖完整的合并区域；同一人别处的空任务仍会统计。'],
    ['#merge-settings h2','合并单元格结构','新增、拆分或移动合并区域后刷新一次；仅改文字不必刷新。范围内任意格已填，整项合并任务就算已填。'],
    ['#auto-query','自动查询','开启后服务器按间隔查询，关闭网页也继续。它只更新结果；发邮件需另设模板。'],
    ['#push-toggle','允许通知','只控制当前设备。开启成功会发一条启动通知；业务提醒还需把邮件模板设为该文档的主推送。'],
    ['#settings-form [name=interval_seconds]','自动查询间隔','单位是秒，最短 60 秒；例如 300 表示每 5 分钟查询。只有开启自动查询时才按此间隔运行。'],
    ['#settings-form [name=timeout_seconds]','请求超时','等待腾讯接口的最长秒数。网络较慢可适度调大；这不是查询间隔。'],
    ['label[for=mail-subject]','邮件标题','可插入 {{global.personcount}} 等变量。主推送通知也显示这个标题；标题不能换行。'],
    ['label[for=mail-body]','邮件内容','支持变量和 for 循环；合法引用为蓝色，语法错误标红。标红模板可保存，但不能发送。'],
    ['#schedule-kind','触发时间','每周几：只在所选当天到点后检查，不跨天补发。固定时刻：到点后持续等待条件满足。全部按北京时间。'],
    ['#condition-variable','触发条件','例如 global.personcount 大于 0：仍有人未填时提醒。涉及未填数据需到点后成功查询，建议开启自动查询。'],
    ['#condition-value','条件值','数字填整数；布尔值填 true 或 false。null 表示未知，不是 0，需先检查查询状态。'],
    ['#smtp-form [name=smtp_host]','SMTP 服务器','填写邮箱服务商提供的 SMTP 地址；不是邮箱登录网页地址。该设置由所有用户共用。'],
    ['#smtp-form [name=smtp_security]','加密方式','端口和加密方式必须按邮箱服务商说明配对；例如 SSL/TLS 常用 465，STARTTLS 常用 587。'],
    ['#smtp-form [name=smtp_password]','发送密码 / 授权码','通常填邮箱开启 SMTP 后生成的授权码，不是网页登录密码。清空后保存会删除已存授权码。'],
    ['#user-form [name=password]','用户密码','至少 12 个字符。编辑已有用户时留空保留原密码；创建用户时必须填写。'],
    ['#user-form [name=role]','用户权限','普通用户管理自己的文档；管理员还可管理普通用户和发件设置；超级管理员可调整权限。']
  ];
  const bubble = document.createElement('div'); bubble.id='field-tooltip'; bubble.className='field-tooltip'; bubble.role='tooltip'; bubble.hidden=true;document.body.append(bubble);
  let active=null, pinned=false, timer, frame;
  const position=()=>{
    if(!active || bubble.hidden)return;
    const r=active.getBoundingClientRect(),v=window.visualViewport;
    const left=v?.offsetLeft || 0,top=v?.offsetTop || 0,width=v?.width || innerWidth,height=v?.height || innerHeight;
    bubble.style.width=Math.min(340,width-24)+'px';
    const b=bubble.getBoundingClientRect();
    bubble.style.left=Math.max(left+12,Math.min(r.left,left+width-b.width-12))+'px';
    bubble.style.top=Math.max(top+12,Math.min(r.bottom+8+b.height<=top+height-12?r.bottom+8:r.top-b.height-8,top+height-b.height-12))+'px';
  };
  const hide=()=>{clearTimeout(timer);active?.setAttribute('aria-expanded','false');active=null;pinned=false;bubble.hidden=true;};
  const show=button=>{
    clearTimeout(timer);if(active!==button)hide();active=button;
    (button.closest('dialog') || document.body).append(bubble);
    bubble.textContent=button.dataset.tip;bubble.hidden=false;button.setAttribute('aria-expanded','true');position();
  };
  for(const [selector,name,text] of tips)for(const control of document.querySelectorAll(selector)) {
    const button=document.createElement('button');button.type='button';button.className='tip-trigger';button.textContent='?';button.dataset.tip=text;
    button.setAttribute('aria-label',name+'说明');button.setAttribute('aria-describedby',bubble.id);button.setAttribute('aria-expanded','false');
    const label=control.closest('label') || (control.matches('label,legend,h2')?control:null);
    if(label) {
      let heading=label.querySelector(':scope > .label-heading');
      if(!heading){heading=document.createElement('span');heading.className='label-heading';const text=document.createElement('span');text.className='label-text';for(const child of [...label.childNodes])if(child.nodeType===Node.TEXT_NODE)text.append(child);heading.append(text);label.prepend(heading);}
      heading.append(button);
    } else {const wrap=document.createElement('span');wrap.className='tip-action';control.before(wrap);wrap.append(control,button);}
    button.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse'){clearTimeout(timer);timer=setTimeout(()=>show(button),160);}});
    button.addEventListener('pointerleave',()=>{if(!pinned)timer=setTimeout(hide,180);});
    button.addEventListener('focus',()=>show(button));
    button.addEventListener('blur',()=>{if(!pinned)hide();});
    button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();if(active===button&&pinned)hide();else{show(button);pinned=true;}});
  }
  bubble.addEventListener('pointerenter',()=>clearTimeout(timer));bubble.addEventListener('pointerleave',()=>{if(!pinned)timer=setTimeout(hide,180);});
  document.addEventListener('pointerdown',event=>{if(active&&!active.contains(event.target)&&!bubble.contains(event.target))hide();},true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&active){event.preventDefault();event.stopPropagation();hide();}},true);
  const track=()=>{cancelAnimationFrame(frame);frame=requestAnimationFrame(position);};
  addEventListener('scroll',track,true);addEventListener('resize',track);visualViewport?.addEventListener('resize',track);
})();
