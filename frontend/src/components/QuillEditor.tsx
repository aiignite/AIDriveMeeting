/**
 * QuillEditor – Quill 富文本编辑器封装组件
 * 使用 react-quill-new (支持 React 18/19)
 * 支持图片粘贴（转 base64 内嵌）
 */
import React, { useMemo, useRef, useCallback, useEffect, useLayoutEffect } from 'react';
import ReactQuill from 'react-quill-new';
import 'react-quill-new/dist/quill.snow.css';

interface QuillEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  readOnly?: boolean;
  isDark?: boolean;
  minHeight?: string;
  /** 是否填满父容器高度（父容器需有明确高度，常配合 flex-1 使用） */
  fillHeight?: boolean;
  /** 是否在工具栏显示图片按钮并支持图片粘贴 */
  enableImage?: boolean;
}

const BASE_TOOLBAR = [
  [{ header: [1, 2, 3, false] }],
  ['bold', 'italic', 'underline', 'strike'],
  [{ color: [] }, { background: [] }],
  [{ list: 'ordered' }, { list: 'bullet' }],
  [{ indent: '-1' }, { indent: '+1' }],
  ['blockquote', 'code-block'],
  ['link'],
  ['clean'],
];

const IMAGE_TOOLBAR = [
  [{ header: [1, 2, 3, false] }],
  ['bold', 'italic', 'underline', 'strike'],
  [{ color: [] }, { background: [] }],
  [{ list: 'ordered' }, { list: 'bullet' }],
  [{ indent: '-1' }, { indent: '+1' }],
  ['blockquote', 'code-block'],
  ['link', 'image'],
  ['clean'],
];

const QuillEditor: React.FC<QuillEditorProps> = ({
  value,
  onChange,
  placeholder = '请输入内容...',
  readOnly = false,
  isDark = false,
  minHeight = '200px',
  fillHeight = false,
  enableImage = false,
}) => {
  const quillRef = useRef<ReactQuill>(null);

  const modules = useMemo(() => ({
    toolbar: readOnly ? false : (enableImage ? IMAGE_TOOLBAR : BASE_TOOLBAR),
  }), [readOnly, enableImage]);

  // 处理图片粘贴 — 将粘贴的图片转为 base64 内嵌
  const handlePaste = useCallback((e: ClipboardEvent) => {
    if (!enableImage || readOnly) return;
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => {
          const editor = quillRef.current?.getEditor();
          if (!editor) return;
          const range = editor.getSelection(true);
          editor.insertEmbed(range.index, 'image', reader.result as string);
          editor.setSelection(range.index + 1, 0);
        };
        reader.readAsDataURL(file);
        return;
      }
    }
  }, [enableImage, readOnly]);

  useEffect(() => {
    if (!enableImage || readOnly) return;
    const editor = quillRef.current?.getEditor();
    const root = editor?.root;
    if (!root) return;
    root.addEventListener('paste', handlePaste as EventListener);
    return () => root.removeEventListener('paste', handlePaste as EventListener);
  }, [enableImage, readOnly, handlePaste]);

  // fillHeight 模式：直接给 quill 内部 DOM 节点写 inline style，
  // 避免被 react-quill-new 自带 CSS 覆盖。inline style 优先级最高。
  // 用 useLayoutEffect 在 paint 前写好，避免闪烁；每次 render 都跑（无依赖），
  // 确保父容器尺寸变化时（如浏览器缩放）样式立即跟随。
  useLayoutEffect(() => {
    if (!fillHeight) return;
    // editor.root 是 <div class="ql-editor">，向上：
    //   parentElement     = <div class="ql-container">
    //   parentElement.parentElement = <div class="quill">   (ReactQuill 自己的根)
    //   .quill.parentElement        = 我们的 <div class="quill-wrapper">
    const editorEl = quillRef.current?.getEditor()?.root;
    const quillRoot = editorEl?.parentElement?.parentElement;
    const wrapper = quillRoot?.parentElement;
    if (!wrapper) return;
    const container = wrapper.querySelector<HTMLElement>('.ql-container');
    const editor = wrapper.querySelector<HTMLElement>('.ql-editor');
    if (!quillRoot || !container || !editor) return;
    // .quill：撑满 wrapper，建立 flex 高度链
    quillRoot.style.setProperty('display', 'flex', 'important');
    quillRoot.style.setProperty('flex-direction', 'column', 'important');
    quillRoot.style.setProperty('flex', '1 1 auto', 'important');
    quillRoot.style.setProperty('min-height', '0', 'important');
    quillRoot.style.setProperty('height', '100%', 'important');
    quillRoot.style.setProperty('overflow', 'hidden', 'important');
    // ql-container：flex 列布局 + 撑满 .quill 剩余空间（工具栏下方）
    container.style.setProperty('display', 'flex', 'important');
    container.style.setProperty('flex-direction', 'column', 'important');
    container.style.setProperty('flex', '1 1 auto', 'important');
    container.style.setProperty('min-height', '0', 'important');
    container.style.setProperty('height', 'auto', 'important');
    container.style.setProperty('overflow', 'hidden', 'important');
    // ql-editor：固定高度约束 + 内部滚动（height:auto 会导致内容撑开无法滚轮）
    editor.style.setProperty('flex', '1 1 auto', 'important');
    editor.style.setProperty('min-height', '0', 'important');
    editor.style.setProperty('height', '100%', 'important');
    editor.style.setProperty('max-height', '100%', 'important');
    editor.style.setProperty('overflow-y', 'auto', 'important');
    editor.style.setProperty('overscroll-behavior', 'contain', 'important');
  });

  return (
    <div
      className={`quill-wrapper ${isDark ? 'quill-dark' : ''} ${fillHeight ? 'h-full flex flex-col min-h-0' : ''}`}
      style={
        fillHeight
          ? ({ '--quill-min-height': '0', height: '100%', minHeight: '0' } as React.CSSProperties)
          : ({ '--quill-min-height': minHeight } as React.CSSProperties)
      }
    >
      <style>{`
        ${fillHeight ? `
        .quill-wrapper.h-full .quill {
          flex: 1 1 auto;
          min-height: 0;
          height: 100%;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }
        .quill-wrapper.h-full .ql-container {
          flex: 1 1 auto;
          min-height: 0;
          font-size: 14px;
          font-family: inherit;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }
        .quill-wrapper.h-full .ql-editor {
          flex: 1 1 auto;
          min-height: 0;
          height: 100%;
          max-height: 100%;
          overflow-y: auto;
          line-height: 1.7;
          overscroll-behavior: contain;
        }
        ` : `
        .quill-wrapper .ql-container {
          min-height: ${minHeight};
          font-size: 14px;
          font-family: inherit;
        }
        .quill-wrapper .ql-editor {
          min-height: ${minHeight};
          line-height: 1.7;
        }
        `}
        .quill-dark .ql-toolbar {
          background: #374151;
          border-color: #4B5563;
        }
        .quill-dark .ql-toolbar .ql-stroke {
          stroke: #D1D5DB;
        }
        .quill-dark .ql-toolbar .ql-fill {
          fill: #D1D5DB;
        }
        .quill-dark .ql-toolbar .ql-picker-label {
          color: #D1D5DB;
        }
        .quill-dark .ql-container {
          background: #1F2937;
          border-color: #4B5563;
          color: #F3F4F6;
        }
        .quill-dark .ql-editor.ql-blank::before {
          color: #9CA3AF;
        }
        .quill-wrapper .ql-toolbar {
          border-radius: 8px 8px 0 0;
          border-color: #E5E7EB;
          flex-shrink: 0;
        }
        .quill-wrapper .ql-container {
          border-radius: 0 0 8px 8px;
          border-color: #E5E7EB;
        }
      `}</style>
      <ReactQuill
        ref={quillRef}
        theme="snow"
        value={value}
        onChange={onChange}
        modules={modules}
        placeholder={placeholder}
        readOnly={readOnly}
      />
    </div>
  );
};

export default QuillEditor;
