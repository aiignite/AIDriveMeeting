/**
 * HTML sanitizer for rendering rich-text content in `dangerouslySetInnerHTML`.
 *
 * 安全实现：基于 DOMPurify（业界标准净化库），替代此前的正则净化方案。
 * 旧正则方案存在已知绕过（<svg/onload=...>、换行绕过 \s+on、data: URL 等），
 * DOMPurify 默认配置已覆盖：剥离 <script>/<style>/on* 事件/javascript: URL、
 * 防御 mXSS 变异攻击。
 *
 * 使用方式保持不变：`sanitizeRichText(html)` —— 所有调用方零改动。
 */
import DOMPurify from 'dompurify';

/**
 * 净化富文本 HTML，返回可安全注入 innerHTML 的字符串。
 * @param html 原始 HTML（如 Quill 编辑器产出或后端存储的描述字段）
 * @returns 净化后的 HTML（script/style/事件属性/危险协议已剥离）
 */
export function sanitizeRichText(html: string | null | undefined): string {
  if (!html) return '';
  return DOMPurify.sanitize(html, {
    // 允许常见富文本标签与样式属性（Quill/BlockNote 场景）
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick'],
  });
}

export default sanitizeRichText;
