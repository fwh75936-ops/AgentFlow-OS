/** 相对时间：刚刚 / X 分钟前 / X 小时前 / X 天前；缺失或非法返回「暂无记录」。 */
export function fmtAgo(iso?: string | null): string {
  if (!iso) return "暂无记录";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "暂无记录";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}
