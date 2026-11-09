"""
rag.py – Build and query a retrieval-augmented generation (RAG) pipeline.

The pipeline:
  1. Accepts a list of document chunks (dicts with 'source' and 'text').
  2. Splits them into smaller chunks using LangChain's text splitter.
  3. Embeds them with OpenAI embeddings and stores them in a FAISS index.
  4. At query time, retrieves the most relevant chunks and passes them
     together with the user's question to an OpenAI chat model.
"""
from __future__ import annotations

import os
from typing import List

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain.schema import Document
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate


SYSTEM_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template="""You are an expert business analyst. You have been given content extracted
from company documents (presentations and spreadsheets). Use ONLY the information
in the context below to answer the user's question. If the answer is not contained
in the context, say so clearly.

Context:
{context}

Question: {question}

Answer (be concise, data-driven, and highlight key numbers or insights where relevant):""",
)


class DocumentChatbot:
    """Manages a per-session FAISS index and answers questions against it."""

    def __init__(self) -> None:
        self._api_key = os.getenv("OPENAI_API_KEY", "")
        self._vectorstore: FAISS | None = None
        self._qa_chain: RetrievalQA | None = None
        self._doc_names: List[str] = []

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def build_index(self, chunks: List[dict]) -> None:
        """
        Build (or rebuild) the FAISS index from a list of
        {"source": str, "text": str} dicts.
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=800,
            chunk_overlap=100,
        )
        documents: List[Document] = []
        for chunk in chunks:
            sub_docs = splitter.create_documents(
                [chunk["text"]],
                metadatas=[{"source": chunk["source"]}],
            )
            documents.extend(sub_docs)

        embeddings = OpenAIEmbeddings(api_key=self._api_key)
        self._vectorstore = FAISS.from_documents(documents, embeddings)
        self._qa_chain = self._build_chain()

    def add_chunks(self, chunks: List[dict]) -> None:
        """Add more chunks to an existing index (incremental upload)."""
        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
        documents: List[Document] = []
        for chunk in chunks:
            sub_docs = splitter.create_documents(
                [chunk["text"]],
                metadatas=[{"source": chunk["source"]}],
            )
            documents.extend(sub_docs)

        embeddings = OpenAIEmbeddings(api_key=self._api_key)
        if self._vectorstore is None:
            self._vectorstore = FAISS.from_documents(documents, embeddings)
        else:
            new_store = FAISS.from_documents(documents, embeddings)
            self._vectorstore.merge_from(new_store)

        self._qa_chain = self._build_chain()

    def _build_chain(self) -> RetrievalQA:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            api_key=self._api_key,
        )
        retriever = self._vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 6},
        )
        return RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            chain_type_kwargs={"prompt": SYSTEM_PROMPT},
            return_source_documents=True,
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(self, question: str) -> dict:
        """
        Run a question against the loaded documents.
        Returns {"answer": str, "sources": [str]}.
        """
        if self._qa_chain is None:
            return {
                "answer": "No documents have been uploaded yet. Please upload a presentation or spreadsheet first.",
                "sources": [],
            }

        result = self._qa_chain.invoke({"query": question})
        sources = list(
            {doc.metadata.get("source", "Unknown") for doc in result.get("source_documents", [])}
        )
        return {"answer": result["result"], "sources": sources}

    def is_ready(self) -> bool:
        return self._vectorstore is not None

    def document_names(self) -> List[str]:
        return self._doc_names

    def add_document_name(self, name: str) -> None:
        if name not in self._doc_names:
            self._doc_names.append(name)
