import { create } from 'zustand';
import { SemanticProfile } from '../types/memory';

export interface StudentProfile extends SemanticProfile {
  displayName: string;
  /** True once the student has picked a class and at least one subject. */
  onboarded: boolean;
  /** Running quiz score across sessions. Persisted with the profile. */
  points: number;
}

const STORAGE_KEY = 'ragnous_profile';

/** The only three classes and three subjects Ragnous covers. */
export const ALLOWED_GRADES = ['8th', '9th', '10th'] as const;
export const ALLOWED_SUBJECTS = ['Science', 'English', 'Social Science'] as const;
export type Grade = typeof ALLOWED_GRADES[number];
export type Subject = typeof ALLOWED_SUBJECTS[number];

const DEFAULT_PROFILE: StudentProfile = {
  displayName: 'Student',
  grade: '8th',
  board: 'CBSE',
  subjects: [],
  languagePreference: 'english',
  interests: [],
  weakSubjects: [],
  onboarded: false,
  points: 0,
};

const sanitize = (raw: Partial<StudentProfile>): StudentProfile => {
  const merged = { ...DEFAULT_PROFILE, ...raw };
  // Old profiles may hold a class we no longer support (11th/12th). Snap them
  // back to the closest allowed value so the app doesn't render a bogus badge.
  if (!ALLOWED_GRADES.includes(merged.grade as Grade)) {
    merged.grade = '10th';
    merged.onboarded = false;
  }
  merged.subjects = (merged.subjects || []).filter(s =>
    ALLOWED_SUBJECTS.includes(s as Subject)
  );
  if (merged.subjects.length === 0) merged.onboarded = false;
  return merged;
};

const loadProfile = (): StudentProfile => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? sanitize(JSON.parse(raw)) : DEFAULT_PROFILE;
  } catch {
    return DEFAULT_PROFILE;
  }
};

const saveProfile = (profile: StudentProfile) => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(profile)); } catch {}
};

interface ProfileState {
  profile: StudentProfile;
  setProfile: (profile: StudentProfile) => void;
  updateProfile: (updates: Partial<StudentProfile>) => void;
  addPoints: (delta: number) => void;
}

/** Settings survive a reload — a preference you have to re-enter is not a preference. */
export const useProfileStore = create<ProfileState>((set) => ({
  profile: loadProfile(),

  setProfile: (profile) => {
    const clean = sanitize(profile);
    saveProfile(clean);
    set({ profile: clean });
  },

  updateProfile: (updates) =>
    set(state => {
      const next = sanitize({ ...state.profile, ...updates });
      saveProfile(next);
      return { profile: next };
    }),

  addPoints: (delta) =>
    set(state => {
      const next = { ...state.profile, points: Math.max(0, (state.profile.points || 0) + delta) };
      saveProfile(next);
      return { profile: next };
    }),
}));
