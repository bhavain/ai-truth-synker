"""ChromaDB vector store for semantic context retrieval"""

import logging
from typing import Optional
from functools import lru_cache
from datetime import datetime

import chromadb
from chromadb.config import Settings as ChromaSettings
from langchain_openai import OpenAIEmbeddings

from app.config import get_settings

logger = logging.getLogger(__name__)


class VectorStore:
    """Client for ChromaDB vector database"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embeddings = OpenAIEmbeddings(
            openai_api_key=self.settings.openai_api_key,
            model="text-embedding-3-small"
        )

        # Initialize ChromaDB client
        self.client = chromadb.HttpClient(
            host=self.settings.chroma_host,
            port=self.settings.chroma_port,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # Create collections for each channel
        self.collections = {
            "supply_chain": self._get_or_create_collection("supply_chain_context"),
            "avionics": self._get_or_create_collection("avionics_context"),
            "management": self._get_or_create_collection("mgmt_context"),
        }

        logger.info("ChromaDB vector store initialized")

    def _get_or_create_collection(self, name: str):
        """Get or create a collection"""
        try:
            return self.client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            logger.error(f"Error creating collection {name}: {e}")
            raise

    def add_context(
        self,
        channel: str,
        summary: str,
        thread_id: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Add a conversation summary to the vector store

        Args:
            channel: Channel name (supply-chain, avionics, management)
            summary: Text summary to embed
            thread_id: Unique thread identifier
            metadata: Additional metadata (topic, participants, etc.)
        """
        if channel not in self.collections:
            logger.warning(f"Unknown channel: {channel}, defaulting to supply_chain")
            channel = "supply_chain"

        collection = self.collections[channel]

        # Generate embedding
        embedding = self.embeddings.embed_query(summary)

        # Prepare metadata
        doc_metadata = {
            "thread_id": thread_id,
            "channel": channel,
            "timestamp": datetime.now().isoformat(),
            **(metadata or {}),
        }

        # Add to collection
        try:
            collection.add(
                embeddings=[embedding],
                documents=[summary],
                metadatas=[doc_metadata],
                ids=[thread_id],
            )
            logger.info(f"Added context to {channel}: {thread_id}")
        except Exception as e:
            logger.error(f"Error adding context: {e}")
            raise

    def query_context(
        self,
        channel: str,
        query: str,
        n_results: int = 3,
    ) -> list[dict]:
        """
        Query for similar historical context

        Args:
            channel: Channel to search
            query: Search query
            n_results: Number of results to return

        Returns:
            List of matching contexts with metadata
        """
        if channel not in self.collections:
            logger.warning(f"Unknown channel: {channel}")
            return []

        collection = self.collections[channel]

        # Generate query embedding
        query_embedding = self.embeddings.embed_query(query)

        # Search
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
            )

            # Format results
            contexts = []
            if results["documents"] and results["documents"][0]:
                for i, doc in enumerate(results["documents"][0]):
                    contexts.append({
                        "summary": doc,
                        "thread_id": results["metadatas"][0][i]["thread_id"],
                        "timestamp": results["metadatas"][0][i].get("timestamp"),
                        "distance": results["distances"][0][i] if "distances" in results else None,
                    })

            logger.info(f"Found {len(contexts)} relevant contexts for query in {channel}")
            return contexts

        except Exception as e:
            logger.error(f"Error querying context: {e}")
            return []

    def get_all_contexts(self, channel: str) -> list[dict]:
        """Get all contexts for a channel (for debugging)"""
        if channel not in self.collections:
            return []

        collection = self.collections[channel]

        try:
            results = collection.get()
            contexts = []

            if results["documents"]:
                for i, doc in enumerate(results["documents"]):
                    contexts.append({
                        "summary": doc,
                        "thread_id": results["metadatas"][i]["thread_id"],
                        "metadata": results["metadatas"][i],
                    })

            return contexts
        except Exception as e:
            logger.error(f"Error getting all contexts: {e}")
            return []

    def clear_collection(self, channel: str) -> None:
        """Clear all documents from a channel collection"""
        if channel not in self.collections:
            return

        try:
            self.client.delete_collection(f"{channel}_context")
            self.collections[channel] = self._get_or_create_collection(f"{channel}_context")
            logger.info(f"Cleared collection: {channel}")
        except Exception as e:
            logger.error(f"Error clearing collection: {e}")


@lru_cache()
def get_vector_store() -> VectorStore:
    """Get cached vector store instance"""
    return VectorStore()
