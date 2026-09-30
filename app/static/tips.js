'use strict';
// Keep only non-obvious choices here. Full instructions belong in the guide.
(() => {
  const tips = [
    ['[name=access_token]', 'Access Token', '到期后去腾讯平台重置。清空后保存会删除现有凭据。'],
    ['.distribution-options legend', '分布方式', '姓名向下排选“列分布”，向右排选“行分布”。'],
    ['#rule-form [name=variable_name]', '规则变量名', '留空会自动命名。改名后，邮件里的引用也要改。'],
    ['#rule-form [name=target_columns]', '需要检查的列', '多列用英文逗号分隔，如 C,D。任意一格有内容就算已填。'],
    ['#rule-form [name=target_rows]', '需要检查的行', '多行用英文逗号分隔，如 3,4。任意一格有内容就算已填。'],
    ['#merge-settings h2', '合并单元格结构', '改过合并区域后刷新一次；只改文字不用刷新。'],
    ['#auto-query', '自动查询', '关闭网页后仍会查询。发邮件还需配置邮件模板。'],
    ['#push-toggle', '允许通知', '只控制这台设备。接收提醒还需选一个“主推送”模板。'],
    ['label[for=mail-body]', '邮件内容', '变量写成 {{变量名}}。蓝色表示有效，红色表示有误。'],
    ['#schedule-kind', '触发时间', '均按北京时间。每周检查仅限当天，固定时刻会持续等到条件满足。'],
    ['#condition-variable', '触发条件', '例如：global.personcount 大于 0，表示还有人未填才提醒。'],
    ['#smtp-form [name=smtp_password]', '发送密码 / 授权码', '一般填邮箱的 SMTP 授权码，不是登录密码。'],
    ['#user-form [name=password]', '用户密码', '至少 12 个字符。修改已有用户时，留空表示不改密码。']
  ];
  for (const [selector, name, text] of tips) {
    for (const control of document.querySelectorAll(selector)) {
      window.WeeklyReportTooltip.attach(control, name, text);
    }
  }
})();
