/** 从 URL 取干净域名，如 https://www.reddit.com/r/... → reddit.com。
 *  合成调研来源(synthetic://survey/...)显示为「模拟调研」而非裸 hostname「survey」。 */
export function domainOf(url?: string): string {
  if (!url) return "来源";
  if (url.startsWith("synthetic://")) return "模拟调研";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "来源";
  }
}

/** favicon 服务（Google s2），非真实域名(模拟调研/来源)或加载失败时降级为字母徽标 */
export function faviconOf(url?: string, size = 32): string | null {
  const d = domainOf(url);
  if (!d.includes(".")) return null; // 「来源」「模拟调研」等非域名 → 字母徽标
  return `https://www.google.com/s2/favicons?domain=${d}&sz=${size}`;
}

const BIAS_LABEL: Record<string, string> = {
  vendor_claim: "厂商官方",
  user_generated: "用户内容",
  third_party: "第三方",
  synthetic: "合成数据",
};

export function biasLabel(bias?: string): string {
  return (bias && BIAS_LABEL[bias]) || bias || "";
}
