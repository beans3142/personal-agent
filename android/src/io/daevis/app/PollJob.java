package io.daevis.app;

import android.app.job.JobParameters;
import android.app.job.JobService;

/** Periodic background check for alarm jobs queued by Daevis. */
public final class PollJob extends JobService {
    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            Alarms.sync(this);
            jobFinished(params, false);
        }, "daevis-alarm-poll").start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;  // Try again later.
    }
}
