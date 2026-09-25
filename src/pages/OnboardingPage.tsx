import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import {
  ALLOWED_GRADES,
  ALLOWED_SUBJECTS,
  Grade,
  Subject,
  useProfileStore,
} from '../store/profileStore';
import { Logo } from '../components/layout/Logo';

const STEPS = ['Class', 'Subjects'];

export default function OnboardingPage() {
  const navigate = useNavigate();
  const updateProfile = useProfileStore(state => state.updateProfile);
  const existing = useProfileStore(state => state.profile);

  const [step, setStep] = useState(0);
  const [grade, setGrade] = useState<Grade>((existing.grade as Grade) || '8th');
  const [subjects, setSubjects] = useState<Subject[]>(
    (existing.subjects as Subject[]).filter(s => ALLOWED_SUBJECTS.includes(s))
  );

  const toggleSubject = (subject: Subject) =>
    setSubjects(list =>
      list.includes(subject) ? list.filter(s => s !== subject) : [...list, subject]
    );

  const finish = () => {
    updateProfile({
      grade,
      subjects,
      // The whole point of onboarding — a chat can now open with a real class
      // and subject already picked, so the composer stops asking every time.
      onboarded: true,
    });
    navigate('/tutor', { replace: true });
  };

  const canContinue = step !== 1 || subjects.length > 0;

  return (
    <div className="min-h-[100dvh] flex flex-col items-center justify-center px-5 py-10 bg-canvas">
      <div className="mb-8">
        <Logo size={30} />
      </div>

      <div className="w-full max-w-md">
        {/* Progress */}
        <div className="flex items-center gap-2 mb-8">
          {STEPS.map((label, i) => (
            <div key={label} className="flex-1">
              <div
                className={`h-1 rounded-full transition-colors ${
                  i <= step ? 'bg-ink' : 'bg-surface-3'
                }`}
              />
              <p className={`mt-2 text-[0.75rem] ${i <= step ? 'text-ink' : 'text-ink-4'}`}>
                {label}
              </p>
            </div>
          ))}
        </div>

        {step === 0 && (
          <div className="animate-fadeUp">
            <h1 className="text-[1.375rem] font-semibold text-ink mb-1.5">Which class are you in?</h1>
            <p className="text-[0.875rem] text-ink-3 mb-6">
              Ragnous is built for Class 8, 9 and 10 — answers stay pinned to your year's NCERT books.
            </p>
            <div className="flex flex-wrap gap-2">
              {ALLOWED_GRADES.map(g => (
                <button
                  key={g}
                  onClick={() => setGrade(g)}
                  className={`chip ${grade === g ? 'chip-active' : ''}`}
                >
                  Class {g}
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="animate-fadeUp">
            <h1 className="text-[1.375rem] font-semibold text-ink mb-1.5">What are you studying?</h1>
            <p className="text-[0.875rem] text-ink-3 mb-6">
              Pick one or more — Ragnous covers Science, English and Social Science for your class.
            </p>
            <div className="flex flex-wrap gap-2">
              {ALLOWED_SUBJECTS.map(s => (
                <button
                  key={s}
                  onClick={() => toggleSubject(s)}
                  className={`chip ${subjects.includes(s) ? 'chip-active' : ''}`}
                >
                  {subjects.includes(s) && <Check className="w-3.5 h-3.5" strokeWidth={2.2} />}
                  {s}
                </button>
              ))}
            </div>
            {subjects.length === 0 && (
              <p className="mt-3 text-[0.75rem] text-ink-2">Choose at least one subject.</p>
            )}
          </div>
        )}

        <div className="mt-8 flex items-center justify-between gap-3">
          <button
            onClick={() => setStep(s => Math.max(0, s - 1))}
            disabled={step === 0}
            className="btn btn-quiet"
          >
            <ArrowLeft className="w-4 h-4" strokeWidth={1.8} />
            Back
          </button>

          {step < STEPS.length - 1 ? (
            <button
              onClick={() => setStep(s => s + 1)}
              disabled={!canContinue}
              className="btn btn-primary"
            >
              Continue
              <ArrowRight className="w-4 h-4" strokeWidth={1.8} />
            </button>
          ) : (
            <button onClick={finish} disabled={!canContinue} className="btn btn-primary">
              Start learning
              <ArrowRight className="w-4 h-4" strokeWidth={1.8} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
