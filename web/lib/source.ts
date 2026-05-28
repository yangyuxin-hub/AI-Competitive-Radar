/** 从 URL 取干净域名，如 https://www.reddit.com/r/... → reddit.com */
export function domainOf(url?: string): string {
  if (!url) return "来源";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "来源";
  }
}

/** favicon 服务（Google s2），加载失败时由调用方降级为字母徽标 */
export function faviconOf(url?: string, size = 32): string | null {
  const d = domainOf(url);
  if (d === "来源") return null;
  return `https://www.google.com/s2/favicons?domain=${d}&sz=${size}`;
}

const BIAS_LABEL: Record<string, string> = {
  vendor_claim: "厂商官方",
  user_generated: "用户内容",
  third_party: "第三方",
};

export function biasLabel(bias?: string): string {
  return (bias && BIAS_LABEL[bias]) || bias || "";
}
