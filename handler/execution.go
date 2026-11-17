package handler

import "fmt"

// Reservations cover preparation, queueing, containers, cleanup and final writes.
// recoveryMu orders admission/start/release with recovery; executionMu protects
// only this registry and FIFO. Neither is held while a container executes.
type execution struct {
	id     string
	active bool
	slot   bool
	run    func(error)
}

// The caller holds recoveryMu and jobsMu (including the stopping check).
func (s *Server) reserveExecution(id string) (*execution, error) {
	if s.Config.Runtime.MaxConcurrentJobs < 0 {
		return nil, fmt.Errorf("runtime.max_concurrent_jobs must be positive")
	}
	s.executionMu.Lock()
	defer s.executionMu.Unlock()
	if s.executions[id] != nil {
		return nil, fmt.Errorf("wait for queued or active execution of this run")
	}
	if s.executions == nil {
		s.executions = make(map[string]*execution)
	}
	e := &execution{id: id}
	s.executions[id] = e
	return e, nil
}

func (s *Server) enqueueExecution(e *execution, run func(error)) {
	s.executionMu.Lock()
	defer s.executionMu.Unlock()
	e.run = run
	s.executionQueue = append(s.executionQueue, e)
	s.dispatchExecutionsLocked()
}

func (s *Server) dispatchExecutionsLocked() {
	if s.executionDispatching || len(s.executionQueue) == 0 {
		return
	}
	s.executionDispatching = true
	go s.dispatchExecutions()
}

func (s *Server) dispatchExecutions() {
	limit := s.Config.Runtime.MaxConcurrentJobs
	if limit == 0 {
		limit = defaultMaxConcurrentJobs
	}
	for {
		s.recoveryMu.Lock()
		s.executionMu.Lock()
		if s.executionSlots >= limit || len(s.executionQueue) == 0 {
			s.executionDispatching = false
			s.executionMu.Unlock()
			s.recoveryMu.Unlock()
			return
		}
		e := s.executionQueue[0]
		s.executionQueue[0] = nil
		s.executionQueue = s.executionQueue[1:]
		e.slot = true
		s.executionSlots++
		s.executionMu.Unlock()
		err := s.reconcileExecutionsLocked()
		if err == nil {
			s.executionMu.Lock()
			e.active = true
			s.executionMu.Unlock()
		}
		s.recoveryMu.Unlock()
		go func() {
			defer s.releaseExecution(e)
			e.run(err)
		}()
	}
}

func (s *Server) releaseExecution(e *execution) {
	s.recoveryMu.Lock()
	defer s.recoveryMu.Unlock()
	s.executionMu.Lock()
	defer s.executionMu.Unlock()
	delete(s.executions, e.id)
	if e.slot {
		s.executionSlots--
	}
	s.dispatchExecutionsLocked()
}
