"""Streamlit-интерфейс. Здесь только UI — логика живёт в core/."""

import streamlit as st

from core.llm import chat_stream

st.set_page_config(page_title="imokazakhstan", page_icon="🇰🇿")
st.title("imokazakhstan")

SYSTEM_PROMPT = "Ты полезный ассистент. Отвечай кратко и по делу."

with st.sidebar:
    provider = st.radio("Модель", ["openai", "nvidia"], format_func=str.upper)
    if st.button("Очистить чат"):
        st.session_state.messages = []

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Напиши сообщение..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            history = [{"role": "system", "content": SYSTEM_PROMPT}, *st.session_state.messages]
            answer = st.write_stream(chat_stream(history, provider))
        except Exception as e:  # показываем ошибку в UI, а не роняем приложение на демо
            answer = f"⚠️ Ошибка: {e}"
            st.error(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
