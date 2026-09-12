// 全局状态锁，防止重复渲染
let isGlossRendered = false;

document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('latin-content');
    const toggleBtn = document.getElementById('toggle-gloss-btn');
    
    if (!container || !toggleBtn) return;

    toggleBtn.addEventListener('click', async () => {
        // --- 第一步：如果从未加载过数据，先执行 fetch 和渲染 ---
        if (!isGlossRendered) {
            const originalBtnText = toggleBtn.textContent;
            toggleBtn.textContent = '正在获取标注...';
            toggleBtn.disabled = true; // 防止连续点击

            const data = await fetchAndRenderGloss(container);

            if (data && data.gloss) {
                renderWithInteraction(container, data.gloss);
                isGlossRendered = true; // 标记已渲染完毕
                container.classList.add('show-all-labels');
                toggleBtn.textContent = '隐藏全文标注';
            } else {
                toggleBtn.textContent = '加载失败，请重试';
                console.error("未能获取到有效的 gloss 数据");
            }
            toggleBtn.disabled = false;
            return;
        }

        // --- 第二步：如果已经渲染过了，仅切换 CSS 类名 ---
        const isShowing = container.classList.toggle('show-all-labels');
        toggleBtn.textContent = isShowing ? '隐藏全文标注' : '显示全文标注';
    });
});

/**
 * 核心请求函数：仅负责 API 通信
 */
async function fetchAndRenderGloss(container) {
    const workId = container.dataset.workid;
    const path = container.dataset.path;

    try {
        const response = await fetch(`/api/gloss?work_id=${workId}&path=${path}`);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return await response.json();
    } catch (error) {
        console.error("Fetch error:", error);
        return null;
    }
}

/**
 * DOM 转换函数：将纯文本节点替换为带交互的 span 结构
 */
function renderWithInteraction(container, glossArray) {
    if (!glossArray || glossArray.length === 0) return;

    // 使用 TreeWalker 找到所有原始文本节点
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    let nodes = [];
    while(walker.nextNode()) {
        nodes.push(walker.currentNode);
    }

    let wordIndex = 0;

    nodes.forEach(node => {
        const text = node.nodeValue;
        // 使用正则切分单词（包含拉丁和希腊字母补充）
        // 括号 () 确保 split 后的数组包含匹配到的单词本身
        const tokens = text.split(/([a-zA-ZÀ-ÿ\u0370-\u03FF]+)/g);

        const fragment = document.createDocumentFragment();

        tokens.forEach(token => {
            // 检查是否为单词且数据未耗尽
            if (/[a-zA-ZÀ-ÿ\u0370-\u03FF]+/.test(token) && wordIndex < glossArray.length) {
                const item = glossArray[wordIndex];
                const span = document.createElement('span');
                span.className = 'word-span';
                span.innerHTML = `
                    <span class="main-word">${token}</span>
                    <div class="gloss-sub">
                        <div class="m">${item.m || ''}</div>
                        <div class="g">${item.g || ''}</div>
                    </div>
                `;
                
                // 单个词点击切换（局部交互）
                span.onclick = (e) => {
                    e.stopPropagation();
                    span.classList.toggle('active');
                };
                
                fragment.appendChild(span);
                wordIndex++;
            } else {
                // 标点、空格或超出范围的词，保持原始文本节点
                fragment.appendChild(document.createTextNode(token));
            }
        });

        // 执行物理替换：将原文本节点替换为包装后的片段
        if (node.parentNode) {
            node.parentNode.replaceChild(fragment, node);
        }
    });
}
