'use strict';
// Runs in the main process: it continues when the renderer is hidden or offline.
class NotificationClient {
  constructor({ request, notify, persist, changed = () => {}, state = {} }) {
    this.request = request; this.notify = notify; this.persist = persist; this.changed = changed;
    this.state = { enabled: state.enabled === true && typeof state.userId === 'string',
      userId: typeof state.userId === 'string' ? state.userId : '',
      cursor: Number.isSafeInteger(state.cursor) && state.cursor >= 0 ? state.cursor : 0 };
    this.generation = 0; this.polling = false; this.error = '';
  }
  status() { return { ...this.state, error: this.error }; }
  write(state, error = '') {
    this.state = state; this.error = error; this.persist(state); this.changed(this.status());
    return this.status();
  }
  disable(error = '') {
    this.generation++;
    return this.write({ enabled: false, userId: '', cursor: 0 }, error);
  }
  async bind(userId) {
    const me = await this.request('/api/me');
    if (typeof userId !== 'string' || me.user?.id !== userId) {
      this.disable(); throw new Error('帐号已变更，请刷新页面。');
    }
    if (this.state.userId && this.state.userId !== userId) this.disable();
    return this.status();
  }
  async enable(userId) {
    const token = ++this.generation;
    await this.bind(userId);
    // Switching accounts invalidates previous readers; use the new generation.
    if (token !== this.generation) throw new Error('帐号已变更，请重新开启通知。');
    if (this.state.enabled && this.state.userId === userId) return this.status();
    const feed = await this.request('/api/notifications?after=latest');
    if (token !== this.generation) return this.status();
    if (feed.user_id !== userId || !Number.isSafeInteger(feed.next_cursor)) throw new Error('帐号已变更，请刷新页面。');
    await this.notify({ title: '消息推送启动成功！', id: 'enabled', document_id: null });
    if (token !== this.generation) return this.status();
    return this.write({ enabled: true, userId, cursor: feed.next_cursor });
  }
  async poll() {
    if (this.polling || !this.state.enabled) return;
    this.polling = true;
    const token = this.generation, uid = this.state.userId;
    try {
      const feed = await this.request('/api/notifications?after=' + this.state.cursor);
      if (token !== this.generation || !this.state.enabled) return;
      if (feed.user_id !== uid) { this.disable('帐号已变更，请重新开启通知。'); return; }
      if (!Array.isArray(feed.items)) throw new Error('Invalid feed');
      for (const item of feed.items) {
        if (token !== this.generation || !this.state.enabled) return;
        if (!Number.isSafeInteger(item.id) || item.id <= this.state.cursor) continue;
        if (item.expires_at > Date.now() / 1000 && typeof item.title === 'string') await this.notify(item);
        if (token !== this.generation || !this.state.enabled) return;
        this.write({ ...this.state, cursor: item.id });
      }
      if (this.error) { this.error = ''; this.changed(this.status()); }
    } catch (error) {
      if (token !== this.generation) return;
      if (error.status === 401) this.disable('登录已失效，请重新登录并开启通知。');
      else { this.error = '暂时无法接收通知，正在自动重试。'; this.changed(this.status()); }
    } finally { this.polling = false; }
  }
}
module.exports = { NotificationClient };
