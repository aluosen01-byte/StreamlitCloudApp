import streamlit as st
from datetime import datetime
import base64
import time
import re
import requests
import io
import os
import zipfile
from PIL import Image
from openai import OpenAI

# =========================================================
# ProAI 配置
# =========================================================
P_BASE_URL = "https://proaiapi.tech/v1"
P_MODEL = "gpt-image-2"

# =========================================================
# 隐藏 Streamlit 默认元素
# =========================================================
hide_st_style = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
</style>
"""

st.markdown(hide_st_style, unsafe_allow_html=True)

# =========================================================
# 0. 安全逻辑锁
# =========================================================
def check_security_lock():
    deadline = datetime(2026, 7, 11, 17, 15)

    if datetime.now() >= deadline:
        st.stop()


check_security_lock()

# =========================================================
# 1. 页面配置
# =========================================================
st.set_page_config(
    page_title="贝歌流水线 v40.5",
    layout="wide"
)

# =========================================================
# 2. 工具函数
# =========================================================
def load_c(fn, d):

    try:

        p = os.path.join(os.getcwd(), fn)

        if os.path.exists(p):

            with open(
                p,
                "r",
                encoding="utf-8-sig"
            ) as f:

                return f.read().strip()

    except:
        pass

    return d


def extract_url(text):
    """
    从返回内容中提取 URL
    """

    match = re.search(
        r'\((https?://[^\)]+)\)',
        text
    )

    if match:
        return match.group(1)

    match = re.search(
        r'(https?://[^\s\x80-\xff]+)',
        text
    )

    if match:
        return match.group(1).strip()

    return text.strip()


def split_blocks(txt: str) -> list[str]:

    txt = re.sub(
        r"```|'''",
        '',
        txt
    )

    pattern = (
        r'(?m)^'
        r'(?:(?:\d+[\.、\s]+)'
        r'|(?:场景\s*\d+[:：\s]*)'
        r'|(?:描述词\s*\d+[:：\s]*)'
        r'|(?:[-*]\s+))'
    )

    parts = re.split(pattern, txt)

    return [
        p.strip()
        for p in parts
        if p.strip()
    ]


# =========================================================
# 3. session_state 初始化
# =========================================================
if 'pool' not in st.session_state:
    st.session_state.pool = {}

if 'wm_bytes' not in st.session_state:
    st.session_state.wm_bytes = None

if 'is_running' not in st.session_state:
    st.session_state.is_running = False

if 'run_mode' not in st.session_state:
    st.session_state.run_mode = None


# =========================================================
# 4. API 工具
# =========================================================
def get_auth():

    try:

        k_key = st.secrets["KIMI_API_KEY"]

        return k_key

    except Exception:

        st.error(
            "❌ 未在 Secrets 中检测到 KIMI_API_KEY"
        )

        st.stop()


def api_vision_safe(
        f_b64,
        prompt,
        retries=3,
        delay=5
):
    """
    Kimi 文本接口
    """

    k = get_auth()

    url = (
        "https://api.moonshot.cn/"
        "v1/chat/completions"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {k}"
    }

    payload = {
        "model": "moonshot-v1-auto",
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        }],
        "temperature": 0.3
    }

    for attempt in range(
            1,
            retries + 1
    ):

        try:

            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60
            )

            res_json = resp.json()

            if 'choices' in res_json:

                return (
                    res_json['choices'][0]
                    ['message']['content']
                    .strip()
                )

            else:

                err_msg = (
                    res_json
                    .get('error', {})
                    .get(
                        'message',
                        '未知接口错误'
                    )
                )

                if (
                        "overloaded"
                        in err_msg.lower()
                        or
                        "busy"
                        in err_msg.lower()
                ):

                    raise RuntimeError(
                        f"Engine overloaded: "
                        f"{err_msg}"
                    )

                else:

                    raise RuntimeError(
                        f"Kimi API 报错: "
                        f"{err_msg}"
                    )

        except Exception as e:

            if attempt < retries:

                st.warning(
                    f"⚠️ Kimi 请求失败 "
                    f"({attempt}/{retries}) "
                    f"等待 {delay}s 后重试: {e}"
                )

                time.sleep(delay)

            else:

                raise RuntimeError(
                    f"❌ Kimi 请求最终失败: {e}"
                )

        time.sleep(1.5)


def api_image(
        f_b64,
        prompt,
        size
):
    """
    ProAI 生图
    """

    try:

        p_key = st.secrets["P_API_KEY"]

    except Exception:

        raise RuntimeError(
            "未配置 P_API_KEY"
        )

    client = OpenAI(
        api_key=p_key,
        base_url=P_BASE_URL
    )

    data_url = (
        f"data:image/jpeg;base64,{f_b64}"
    )

    try:

        res = client.chat.completions.create(
            model=P_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url
                        }
                    }
                ]
            }],
            extra_body={
                "response_format": {
                    "type": "image_url"
                }
            }
        )

        raw_content = (
            res.choices[0]
            .message.content
            .strip()
        )

        img_url = extract_url(raw_content)

        if not img_url.startswith("http"):

            raise RuntimeError(
                f"URL 解析失败: "
                f"{raw_content}"
            )

        img_res = requests.get(
            img_url,
            timeout=120
        )

        img_res.raise_for_status()

        return img_res.content

    except Exception as e:

        raise RuntimeError(
            f"ProAI 生图失败: {e}"
        )


# =========================================================
# 5. 水印工具
# =========================================================
def apply_wm(
        img_bytes,
        wm_bytes
):

    base = Image.open(
        io.BytesIO(img_bytes)
    ).convert("RGBA")

    logo = Image.open(
        io.BytesIO(wm_bytes)
    ).convert("RGBA")

    scale = (
            (base.size[0] * 0.15)
            /
            logo.size[0]
    )

    logo = logo.resize(
        (
            int(logo.size[0] * scale),
            int(logo.size[1] * scale)
        ),
        Image.Resampling.LANCZOS
    )

    tmp = Image.new(
        "RGBA",
        base.size,
        (0, 0, 0, 0)
    )

    tmp.paste(base, (0, 0))

    r, g, b, a = logo.split()

    logo.putalpha(
        a.point(
            lambda i: int(i * 0.5)
        )
    )

    tmp.paste(
        logo,
        (
            base.size[0]
            - logo.size[0]
            - 20,
            20
        ),
        mask=logo
    )

    buf = io.BytesIO()

    tmp.convert("RGB").save(
        buf,
        format="JPEG",
        quality=95
    )

    return buf.getvalue()


# =========================================================
# 6. 核心流水线
# =========================================================
def run_pipeline(mode):

    st.session_state.is_running = True

    st.session_state.run_mode = mode

    for fid, info in st.session_state.pool.items():

        if not st.session_state.is_running:
            break

        try:

            # =====================================================
            # 标题阶段
            # =====================================================
            if (
                    mode in ['title', 'all']
                    and
                    not info["title"]
            ):

                info["status"] = (
                    "正在生成标题..."
                )

                info["title"] = api_vision_safe(
                    info["b64"],
                    st.session_state.t_p_val
                )

            # =====================================================
            # 描述词阶段
            # =====================================================
            if (
                    mode in ['script', 'all']
                    and
                    info["title"]
                    and
                    not info["tasks"]
            ):

                info["status"] = (
                    "正在生成图片描述词..."
                )

                raw_txt = api_vision_safe(
                    info["b64"],
                    st.session_state.s_p_val
                )

                blocks = split_blocks(
                    raw_txt
                )

                info["tasks"] = [
                    {
                        "prompt": b,
                        "img": None,
                        "is_wm": False
                    }
                    for b in blocks
                ]

            # =====================================================
            # 生图阶段
            # =====================================================
            if (
                    mode in ['image', 'all']
                    and
                    info["tasks"]
            ):

                for i, t in enumerate(
                        info["tasks"]
                ):

                    if not st.session_state.is_running:
                        break

                    if not t["img"]:

                        info["status"] = (
                            f"正在生成图片 "
                            f"{i + 1}/"
                            f"{len(info['tasks'])}..."
                        )

                        cur_p = st.session_state.get(
                            f"pa_{fid}_{i}",
                            t["prompt"]
                        )

                        t["img"] = api_image(
                            info["b64"],
                            cur_p,
                            st.session_state.sz_val
                        )

                        time.sleep(1)

            info["status"] = "✅ 已完成"

        except Exception as e:

            info["status"] = (
                f"❌ 失败: {str(e)}"
            )

            st.session_state.is_running = False

            return

    st.session_state.is_running = False


# =========================================================
# 7. 侧边栏
# =========================================================
with st.sidebar:

    st.header("⚙️ 控制面板")

    st.session_state.sz_val = st.selectbox(
        "出图尺寸",
        [
            "2048x2048",
            "1440x2560"
        ]
    )

    if st.session_state.is_running:

        if st.button(
                "🛑 停止执行",
                type="primary",
                use_container_width=True
        ):

            st.session_state.is_running = False

    st.divider()

    wm_f = st.file_uploader(
        "🖼️ 上传水印 (PNG)",
        type=["png"]
    )

    if wm_f:
        st.session_state.wm_bytes = wm_f.getvalue()

    if st.button(
            "🌊 批量添加水印",
            use_container_width=True
    ):

        if st.session_state.wm_bytes:

            for info in st.session_state.pool.values():

                for t in info["tasks"]:

                    if (
                            t["img"]
                            and
                            not t.get("is_wm")
                    ):

                        t["img"] = apply_wm(
                            t["img"],
                            st.session_state.wm_bytes
                        )

                        t["is_wm"] = True

            st.success("水印处理完毕！")

    if st.button(
            "🗑️ 清空任务池",
            use_container_width=True
    ):

        st.session_state.pool = {}


# =========================================================
# 8. 主界面
# =========================================================
st.title("贝歌流水线 v40.5 🚀")

col_p1, col_p2 = st.columns(2)

with col_p1:

    st.session_state.t_p_val = st.text_area(
        "✍️ 标题 Prompt",
        value=load_c(
            "prompt_title.txt",
            "生成标题"
        ),
        height=400
    )

with col_p2:

    st.session_state.s_p_val = st.text_area(
        "📜 脚本 Prompt",
        value=load_c(
            "prompt_photo.txt",
            "拆解脚本"
        ),
        height=400
    )

st.divider()

btns = st.columns([1, 1, 1, 1, 1.5])

if btns[0].button(
        "🚀 全量执行",
        type="primary",
        use_container_width=True
):
    run_pipeline('all')

if btns[1].button(
        "✍️ 批量标题",
        use_container_width=True
):
    run_pipeline('title')

if btns[2].button(
        "📜 批量脚本",
        use_container_width=True
):
    run_pipeline('script')

if btns[3].button(
        "🎨 批量图片",
        use_container_width=True
):
    run_pipeline('image')

if st.session_state.is_running:

    for fid, info in st.session_state.pool.items():

        if "正在" in info["status"]:

            st.info(
                f"🚀 当前正在处理: "
                f"{info['name']} | "
                f"{info['status']}"
            )

# =========================================================
# 上传素材
# =========================================================
up_files = st.file_uploader(
    "📸 批量上传素材 (支持多选)",
    accept_multiple_files=True
)

if up_files:

    for f in up_files:

        fid = f"{f.name}_{f.size}"

        if fid not in st.session_state.pool:

            st.session_state.pool[fid] = {
                "name": f.name,
                "b64": base64.b64encode(
                    f.getvalue()
                ).decode(),
                "raw": f.getvalue(),
                "title": "",
                "tasks": [],
                "status": "⏳ 待命"
            }

if st.session_state.pool:

    for fid, info in st.session_state.pool.items():

        with st.container(border=True):

            cl, cr = st.columns([1, 4])

            with cl:

                st.image(
                    info["raw"],
                    caption=info["name"]
                )

                if st.button(
                        "🔄 重置素材",
                        key=f"rs_{fid}",
                        use_container_width=True
                ):

                    if f"ti_{fid}" in st.session_state:
                        del st.session_state[f"ti_{fid}"]

                    info.update({
                        "title": "",
                        "tasks": [],
                        "status": "⏳ 待命"
                    })

            with cr:

                st.markdown(
                    f"卡片状态: "
                    f":green[`{info['status']}`]"
                )

                key_ti = f"ti_{fid}"

                if (
                        info["title"]
                        and
                        key_ti
                        not in st.session_state
                ):

                    st.session_state[key_ti] = (
                        info["title"]
                    )

                st.text_input(
                    "生成标题 (可微调)",
                    key=key_ti
                )

                if key_ti in st.session_state:
                    info["title"] = (
                        st.session_state[key_ti]
                    )

                if info["tasks"]:

                    st.write("---")

                    sub_cols = st.columns(
                        len(info["tasks"])
                    )

                    for i, t in enumerate(
                            info["tasks"]
                    ):

                        with sub_cols[i]:

                            if t["img"]:

                                st.image(
                                    t["img"],
                                    use_container_width=True
                                )

                                st.download_button(
                                    "📥",
                                    t["img"],
                                    f"sc_{i}.jpg",
                                    key=f"dl_{fid}_{i}"
                                )

                            key_pa = (
                                f"pa_{fid}_{i}"
                            )

                            if (
                                    key_pa
                                    not in st.session_state
                            ):

                                st.session_state[key_pa] = (
                                    t["prompt"]
                                )

                            st.text_area(
                                f"描述词 {i + 1}",
                                key=key_pa,
                                height=100
                            )

                            if (
                                    key_pa
                                    in st.session_state
                            ):

                                t["prompt"] = (
                                    st.session_state[key_pa]
                                )

                            if st.button(
                                    f"🎨 重绘",
                                    key=f"re_{fid}_{i}",
                                    use_container_width=True
                            ):

                                t["img"] = api_image(
                                    info["b64"],
                                    st.session_state[key_pa],
                                    st.session_state.sz_val
                                )

                                t["is_wm"] = False

    # =====================================================
    # ZIP 打包
    # =====================================================
    all_imgs = []

    for fid, info in st.session_state.pool.items():

        for i, t in enumerate(info["tasks"]):

            if t["img"]:

                all_imgs.append(
                    (
                        f"{info['name']}_sc{i + 1}.jpg",
                        t["img"]
                    )
                )

    if all_imgs:

        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w") as z:

            for fn, data in all_imgs:

                z.writestr(fn, data)

        btns[4].download_button(
            f"📦 下载所有图片 "
            f"({len(all_imgs)})",
            buf.getvalue(),
            "all.zip",
            type="primary",
            use_container_width=True
        )

# =========================================================
# 自动刷新
# =========================================================
if st.session_state.is_running:

    time.sleep(0.5)

    st.rerun()