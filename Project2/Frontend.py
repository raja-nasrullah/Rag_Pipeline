# chatbot_frontend.py
import streamlit as st
from RAG_Pipleline import collection, client  # reuse your pipeline

st.set_page_config(page_title="RAG Chatbot", page_icon="🤖", layout="centered")

st.title("🤖 Document Chatbot with ChromaDB")

# ---------------------------
# Chatbot Section
# ---------------------------
if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "query" not in st.session_state:
    st.session_state["query"] = ""

# Display chat history
for msg in st.session_state["messages"]:
    role = "🧑 You" if msg["role"] == "user" else "🤖 Bot"
    st.markdown(f"**{role}:** {msg['content']}")

query = st.text_input("Type your question here:", value=st.session_state["query"], key="query_input")

if st.button("Send"):
    st.session_state["query"] = query  # save query
    if query:
        # Add user message
        st.session_state["messages"].append({"role": "user", "content": query})

        try:
            # Step 1: embed query
            query_embedding = client.embeddings.create(
                model="text-embedding-3-small",
                input=query
            ).data[0].embedding

            # Step 2: query Chroma
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=5,
                include=["documents", "distances"]
            )

            if not results["documents"] or not results["documents"][0]:
                answer = "I couldn’t find anything in your documents."
            else:
                docs = results["documents"][0][:9]
                valid_docs = [d for d in docs if d]
                if not valid_docs:
                    answer = "I don’t know (no valid documents found)."
                else:
                    context_text = "\n".join(valid_docs)

                    # Step 3: Ask GPT
                    response = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[
                            {"role": "system", "content": (
                                "You are a strict assistant. "
                                "Answer ONLY based on the provided document context. "
                                "If the answer is not in the context, reply with: "
                                "'I don’t know based on the documents.'"
                            )},
                            {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"}
                        ]
                    )
                    answer = response.choices[0].message.content.strip()

            # Add bot message
            st.session_state["messages"].append({"role": "bot", "content": answer})

        except Exception as e:
            st.error(f"⚠️ Error answering query: {e}")

        # ✅ Clear input after processing
        st.session_state["query"] = ""
        st.rerun()

# Optional: Clear chat button
if st.button("🗑️ Clear Chat"):
    st.session_state["messages"] = []
    st.session_state["query"] = ""
    st.rerun()
