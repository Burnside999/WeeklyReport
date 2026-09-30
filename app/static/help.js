'use strict';
(async () => {
  if(!await workspaceReady || page !== 'help')return;
  const sections = [...document.querySelectorAll('.guide-section')];
  const search = $('#help-search'), status = $('#help-search-state');
  let previousOpen = null;
  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    if(query && previousOpen === null)previousOpen = sections.map(section => section.open);
    for(const [index, section] of sections.entries()) {
      section.hidden = !!query && !section.textContent.toLocaleLowerCase().includes(query);
      if(query)section.open = !section.hidden;
      else if(previousOpen !== null)section.open = previousOpen[index];
    }
    if(!query)previousOpen = null;
    const count = sections.filter(section => !section.hidden).length;
    status.textContent = query ? (count ? `找到 ${count} 个相关章节` : '没有找到，试试“通知”“未填写”或“变量”。') : '';
  }
  function reveal() {
    const section = sections.find(item => '#'+item.id === location.hash);
    if(!section)return;
    search.value = ''; filter(); section.open = true;
    section.querySelector('summary').focus({preventScroll:true});
    section.scrollIntoView({block:'start'});
  }
  search.addEventListener('input', filter);
  $('#help').addEventListener('click', event => {
    const link = event.target.closest('a[href^="#guide-"]');
    if(!link)return;
    event.preventDefault();
    history.replaceState(null, '', location.pathname+location.search+link.getAttribute('href'));
    reveal();
  });
  addEventListener('hashchange', reveal);
  reveal();
})().catch(error => toast(error.message));
