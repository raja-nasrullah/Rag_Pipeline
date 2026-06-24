# RAG Pipeline Chatbot

A complete Retrieval-Augmented Generation (RAG) system built with Python that automatically retrieves documents from Google Drive, processes and embeds their content, stores embeddings in ChromaDB, and enables intelligent question answering using GPT models.

## Features

* Google Drive integration for automatic document monitoring
* Automatic file download and processing
* Text extraction from documents
* Embedding generation and storage using ChromaDB
* Semantic search and retrieval
* GPT-powered conversational chatbot
* Automatic cleanup when no documents are available
* Dynamic knowledge base updates

## Tech Stack

* Python
* Google Drive API
* LangChain
* ChromaDB
* OpenAI GPT
* OpenAI Embeddings

## Workflow

1. Check Google Drive for documents.
2. Download available files.
3. Extract text from documents.
4. Generate embeddings.
5. Store embeddings in ChromaDB.
6. Retrieve relevant context for user queries.
7. Generate accurate responses using GPT.
8. Clear local storage and vector database when no files are available.

This project demonstrates how Retrieval-Augmented Generation can be used to build intelligent, document-aware chatbots that provide accurate and context-driven responses from dynamically updated data sources.

# Rag_Pipeline

A Python-based RAG pipeline that automatically syncs documents from Google Drive, generates embeddings, stores them in ChromaDB, and powers a GPT-based chatbot for context-aware question answering.
