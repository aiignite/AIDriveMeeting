/**
 * PDF 中文字体加载工具
 * 使用 NotoSansSC 字体支持 PDF 中文输出
 */

let cachedFontBase64: string | null = null;

/**
 * 异步加载中文字体并转为 base64 格式
 * 字体文件放在 public/fonts/ 目录下，首次加载后缓存
 */
export async function loadChineseFontBase64(): Promise<string> {
  if (cachedFontBase64) return cachedFontBase64;

  const response = await fetch('/fonts/NotoSansSC-Regular.ttf');
  if (!response.ok) {
    throw new Error(`Failed to load Chinese font: ${response.statusText}`);
  }
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);

  // Convert ArrayBuffer to base64
  let binary = '';
  const chunkSize = 8192;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSize, bytes.length));
    binary += String.fromCharCode(...chunk);
  }
  cachedFontBase64 = btoa(binary);
  return cachedFontBase64;
}

/**
 * 注册中文字体到 jsPDF 实例
 * 同时注册 normal 和 bold 变体，避免 headStyles.fontStyle='bold' 时回退到 Helvetica 导致乱码
 * @param doc jsPDF 实例
 * @param fontBase64 base64 编码的字体数据
 */
export function registerChineseFont(doc: any, fontBase64: string): void {
  doc.addFileToVFS('NotoSansSC-Regular.ttf', fontBase64);
  doc.addFont('NotoSansSC-Regular.ttf', 'NotoSansSC', 'normal');
  doc.addFont('NotoSansSC-Regular.ttf', 'NotoSansSC', 'bold');
}

/**
 * 设置 jsPDF 实例使用中文字体
 * @param doc jsPDF 实例
 */
export function setChineseFont(doc: any): void {
  doc.setFont('NotoSansSC', 'normal');
}
