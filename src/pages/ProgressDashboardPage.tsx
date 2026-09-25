import { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { CheckCheck, Flame, Info, Sparkles, Target, TrendingUp, Trophy } from 'lucide-react';
import { AppShell } from '../components/layout/AppShell';
import { useSendMessage } from '../hooks/useSendMessage';
import { useProfileStore } from '../store/profileStore';
import {
  last7Days,
  needsPractice,
  overallAccuracy,
  studyStreak,
  useProgressStore,
  weakestTopic,
  weeklyGrowth,
} from '../store/progressStore';

const StatCard = ({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  hint?: string;
}) => (
  <div className="card p-5">
    <div className="flex items-center gap-2.5 mb-3">
      <span className="grid place-items-center w-8 h-8 rounded-lg bg-surface-2">
        <Icon className="w-4 h-4 text-ink-2" strokeWidth={1.8} />
      </span>
      <p className="text-[0.8125rem] text-ink-3">{label}</p>
    </div>
    <p className="text-[1.5rem] font-semibold text-ink leading-none tracking-[-0.02em]">{value}</p>
    {hint && <p className="mt-1.5 text-[0.75rem] text-ink-4">{hint}</p>}
  </div>
);

/** Empty-state hero for when the student has answered zero quiz questions.
 *  Fake numbers here would be worse than saying "nothing yet" honestly. */
const EmptyProgress = () => (
  <div className="card p-8 text-center">
    <div className="mx-auto grid place-items-center w-12 h-12 rounded-full bg-surface-2 border border-line mb-3">
      <Sparkles className="w-5 h-5 text-ink" strokeWidth={1.8} />
    </div>
    <h2 className="text-[1rem] font-semibold text-ink mb-1">
      No quiz attempts yet
    </h2>
    <p className="text-[0.8125rem] text-ink-3 max-w-md mx-auto">
      Take a quiz in a chat — tap <span className="font-medium text-ink">Quiz me</span> at
      the top, or type <span className="font-medium text-ink">"give me a quiz"</span> after
      chatting about a topic. Your accuracy per topic will appear here.
    </p>
  </div>
);

export default function ProgressDashboardPage() {
  const send = useSendMessage();
  const navigate = useNavigate();
  const attempts = useProgressStore(state => state.attempts);
  const points = useProfileStore(state => state.profile.points);

  const stats = useMemo(() => {
    const trend = last7Days(attempts);
    return {
      trend,
      // Recharts skips gaps only when the field is omitted or null; keep it as
      // a number (or 0) for the visible line and mark days with no data via
      // the tooltip label instead.
      trendChart: trend.map(b => ({
        day: b.label,
        score: b.mastery ?? 0,
        hasData: b.attempts > 0,
        attempts: b.attempts,
      })),
      growth: weeklyGrowth(attempts),
      weakest: weakestTopic(attempts),
      streak: studyStreak(attempts),
      accuracy: overallAccuracy(attempts),
      weak: needsPractice(attempts),
      total: attempts.length,
      correct: attempts.filter(a => a.correct).length,
    };
  }, [attempts]);

  const practise = (topic: string) => {
    void send(`Give me five practice questions on ${topic}, then check my answers one at a time.`);
    navigate('/tutor');
  };

  const goToTutor = () => navigate('/tutor');

  return (
    <AppShell title="Progress" contentClassName="mx-auto w-full max-w-5xl px-5 py-8 sm:px-8">
      {attempts.length === 0 ? (
        <>
          <p className="flex items-start gap-2 text-[0.8125rem] text-ink-3 mb-6">
            <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" strokeWidth={1.8} />
            Progress fills in from your quiz answers. Take one to get started.
          </p>
          <EmptyProgress />
        </>
      ) : (
        <>
          <p className="flex items-start gap-2 text-[0.8125rem] text-ink-3 mb-6">
            <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" strokeWidth={1.8} />
            Built from your quiz answers — {stats.correct} correct out of {stats.total} across
            all topics.
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <StatCard
              icon={Trophy}
              label="Points earned"
              value={`${points}`}
              hint="10 pts per correct answer"
            />
            <StatCard
              icon={TrendingUp}
              label="Weekly growth"
              value={
                stats.growth === null
                  ? '—'
                  : `${stats.growth > 0 ? '+' : ''}${stats.growth}%`
              }
              hint={
                stats.growth === null
                  ? 'Need attempts in the last two weeks'
                  : "Against last week's accuracy"
              }
            />
            <StatCard
              icon={Target}
              label="Weakest topic"
              value={stats.weakest ? stats.weakest.topic : '—'}
              hint={
                stats.weakest
                  ? `${stats.weakest.mastery}% mastery · ${stats.weakest.attempts} tries`
                  : 'Need more attempts per topic'
              }
            />
            <StatCard
              icon={Flame}
              label="Study streak"
              value={`${stats.streak} day${stats.streak === 1 ? '' : 's'}`}
              hint={stats.streak > 0 ? 'Consecutive days with a quiz answer' : 'Answer one today to start'}
            />
          </div>

          <div className="card p-5 mb-6">
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-[0.9375rem] font-semibold text-ink">Mastery trend</h2>
              <span className="inline-flex items-center gap-1 text-[0.75rem] text-ink-3">
                <CheckCheck className="w-3.5 h-3.5" strokeWidth={1.8} />
                Overall {stats.accuracy ?? 0}%
              </span>
            </div>
            <p className="text-[0.8125rem] text-ink-3 mb-5">
              Daily accuracy across every quiz you answered this week.
            </p>

            <div className="h-64 -ml-2">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={stats.trendChart} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="masteryFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#0D0D0D" stopOpacity={0.14} />
                      <stop offset="100%" stopColor="#0D0D0D" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#EFEFEF" vertical={false} />
                  <XAxis
                    dataKey="day"
                    stroke="#B4B4B4"
                    tickLine={false}
                    axisLine={false}
                    fontSize={12}
                    dy={6}
                  />
                  <YAxis
                    stroke="#B4B4B4"
                    tickLine={false}
                    axisLine={false}
                    fontSize={12}
                    width={34}
                    domain={[0, 100]}
                  />
                  <Tooltip
                    cursor={{ stroke: '#D9D9D9', strokeWidth: 1 }}
                    contentStyle={{
                      background: '#FFFFFF',
                      border: '1px solid #E8E8E8',
                      borderRadius: 12,
                      boxShadow: '0 8px 28px -8px rgba(13,13,13,0.18)',
                      fontSize: 13,
                    }}
                    labelStyle={{ color: '#0D0D0D', fontWeight: 600 }}
                    formatter={(value: number, _n, item: any) => {
                      const row = item?.payload;
                      if (!row?.hasData) return ['No attempts', 'Mastery'];
                      return [`${value}% · ${row.attempts} answered`, 'Mastery'];
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="score"
                    stroke="#0D0D0D"
                    strokeWidth={2}
                    fill="url(#masteryFill)"
                    dot={{ r: 3, fill: '#0D0D0D', strokeWidth: 0 }}
                    activeDot={{ r: 5, fill: '#0D0D0D', stroke: '#FFFFFF', strokeWidth: 2 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="card p-5">
            <h2 className="text-[0.9375rem] font-semibold text-ink mb-1">Needs practice</h2>
            <p className="text-[0.8125rem] text-ink-3 mb-5">
              Topics where your quiz answers slip most often. Anything above 80% is hidden.
            </p>

            {stats.weak.length === 0 ? (
              <p className="text-[0.8125rem] text-ink-3">
                Nothing under 80% yet — either you are on a roll, or you need a couple more
                quizzes on each topic before a verdict counts.{' '}
                <button onClick={goToTutor} className="text-ink font-medium underline">
                  Take a quiz
                </button>
                .
              </p>
            ) : (
              <div className="divide-y divide-line">
                {stats.weak.map(item => (
                  <div
                    key={item.topic}
                    className="flex items-center gap-4 py-3.5 first:pt-0 last:pb-0"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-[0.875rem] font-medium text-ink truncate">
                        {item.topic}
                      </p>
                      <p className="text-[0.75rem] text-ink-4">
                        {item.subjectArea || 'NCERT topic'} · {item.correct}/{item.attempts}{' '}
                        correct
                      </p>

                      <div className="mt-2 flex items-center gap-2.5">
                        <div className="h-1.5 flex-1 max-w-[16rem] rounded-full bg-surface-3 overflow-hidden">
                          <div
                            className="h-full rounded-full bg-ink"
                            style={{ width: `${item.mastery}%` }}
                          />
                        </div>
                        <span className="text-[0.75rem] text-ink-3 tabular-nums">
                          {item.mastery}%
                        </span>
                      </div>
                    </div>

                    <button
                      onClick={() => practise(item.topic)}
                      className="btn btn-secondary shrink-0"
                    >
                      Practise
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </AppShell>
  );
}
