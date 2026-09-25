export interface SemanticProfile {
  grade: string;
  board: "CBSE";
  subjects: string[];
  languagePreference: "english" | "hindi" | "hinglish";
  interests: string[];
  weakSubjects: string[];
}

export interface EpisodicSummary {
  id: string;
  summary: string;
  sessionDate: string;
  relatedTopics: string[];
}

export interface WeakTopic {
  topicId: string;
  topicName: string;
  subject: string;
  masteryScore: number; // 0-1
}
