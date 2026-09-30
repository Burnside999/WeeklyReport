'use strict';
// All event timestamps use Beijing time, regardless of the device timezone.
const beijingDateTime = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'
});
function formatDateTime(value, empty = '—') {
  if(value === null || value === undefined || value === '')return empty;
  let input = value;
  if(typeof input === 'string') {
    input = input.trim().replace(' ', 'T');
    if(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(input))input += '+08:00';
  }
  const instant = new Date(input);
  if(!Number.isFinite(instant.getTime()))return empty;
  const parts = Object.fromEntries(beijingDateTime.formatToParts(instant).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}
function formatClock(value) {
  return /^\d{2}:\d{2}$/.test(value || '') ? value + ':00' : value;
}
function formatEventText(text) {
  // Existing saved scheduler notes may still contain the previous ISO format.
  return (text || '').replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?/g, value => formatDateTime(value));
}
