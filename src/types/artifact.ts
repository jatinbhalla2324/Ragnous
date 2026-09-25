export type ArtifactType =
  | "mermaid"
  | "r3f_model"
  | "widget_3d"
  | "notes"
  | "interactive_simulator"
  | "image"
  | "youtube"
  | "none";

export interface ArtifactPayload {
  type: ArtifactType;
  data: any; // Shape depends on type, mocked for now
}
